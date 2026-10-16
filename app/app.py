"""Backend of the "Predictive turbine maintenance" app (Databricks Apps, FastAPI).

- Data: databricks-sql-connector against the SQL warehouse declared as an app resource,
  using the app identity (service principal) through the SDK Config().
- Assistant: app/rag.py (multi-query retrieval from AI Search, structured answer, claim
  verification) with a Foundation Model APIs LLM.
- If a table does not exist yet, the endpoints return an explicit empty state (ready=false,
  `missing` lists the tables). Any other failure (SQL error, timeout, warehouse down) is a
  503 {"error": "data_unavailable"}: predictions are never invented or filled with defaults.
- Predictions are NOT computed live. The app shows the batch scoring in `gold_predictions`
  (written by src/03_score.py with models:/...rul_model@champion) and does not call the
  model serving endpoint. The model version and scoring time travel with every row.
"""

from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import decimal
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

import rag
from health_projection import project_health

log = logging.getLogger("maintenance")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuration (environment variables defined in app.yaml)
# ---------------------------------------------------------------------------
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
CATALOG = os.getenv("CATALOG", "workspace")
SCHEMA = os.getenv("SCHEMA", "maintenance")
for _v in (CATALOG, SCHEMA):
    if not _IDENT.fullmatch(_v):
        raise RuntimeError(f"Invalid identifier in the configuration: {_v!r}")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
AI_SEARCH_INDEX = os.getenv("AI_SEARCH_INDEX", f"{CATALOG}.{SCHEMA}.manual_chunks_index")
# Chat endpoint of Foundation Model APIs; hosted models change and get retired (see the Serving tab).
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "databricks-gpt-oss-120b")
RAG_CONFIG = os.getenv("RAG_CONFIG", "v2")
KINDS_TTL = 3600.0  # seconds; the page-kind map only changes when the index is rebuilt
CACHE_TTL = float(os.getenv("CACHE_TTL_SECONDS", "30"))
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"
PREDICTIONS_TABLE = f"{CATALOG}.{SCHEMA}.gold_predictions"
SQL_TIMEOUT = float(os.getenv("SQL_TIMEOUT_SECONDS", "90"))  # socket timeout of the SQL connector
MAX_UNIT = 10_000
NEAR_FAILURE_MAX_RUL = 30.0  # engines with an actual RUL at or below this count as "near failure"
HEALTH_THRESHOLD = 1.0  # health index level that marks the mean failure point
SNIPPET_CHARS = 300

app = FastAPI(title="Predictive turbine maintenance", docs_url=None, redoc_url=None)

# Security headers (audit finding D-06). The CSP's font-src/style-src allowlist matches exactly
# what app/static/index.html loads (Google Fonts) -- nothing broader. index.html's whole frontend
# is one inline <script> block (no bundler/build step for this app), so script-src must allow
# 'unsafe-inline' too, or the dashboard would load with no JavaScript at all. This still blocks
# the things that matter without a build step: loading a script from any other origin, framing
# the page, and MIME-sniffing a response into something executable.
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


class TableUnavailable(Exception):
    """The queried table or view does not exist yet."""


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
_cfg_lock = threading.Lock()
_cfg = None


def _config():
    global _cfg
    with _cfg_lock:
        if _cfg is None:
            from databricks.sdk.core import Config

            _cfg = (
                Config()
            )  # in Apps it reads DATABRICKS_HOST, DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET
        return _cfg


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def _is_missing_table(msg: str) -> bool:
    m = msg.upper()
    return any(
        t in m
        for t in (
            "TABLE_OR_VIEW_NOT_FOUND",
            "SCHEMA_NOT_FOUND",
            "CATALOG_NOT_FOUND",
            "DELTA_TABLE_NOT_FOUND",
            "DOES NOT EXIST",
            "CANNOT BE FOUND",
        )
    )


def run_query(sql_text: str, params: dict | None = None) -> list[dict]:
    """Runs SQL on the warehouse and returns rows as dictionaries."""
    if not WAREHOUSE_ID:
        raise TableUnavailable("DATABRICKS_WAREHOUSE_ID is not set (sql_warehouse app resource)")
    from databricks import sql

    cfg = _config()
    host = re.sub(r"^https?://", "", cfg.host).rstrip("/")
    try:
        with (
            sql.connect(
                server_hostname=host,
                http_path=f"/sql/1.0/warehouses/{WAREHOUSE_ID}",
                credentials_provider=lambda: cfg.authenticate,
                _socket_timeout=SQL_TIMEOUT,
            ) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(sql_text, params or {})
            cols = [d[0] for d in cur.description]
            return [{c: _jsonable(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    except Exception as e:  # the connector raises its own exception types; the message tells them apart
        if _is_missing_table(str(e)):
            raise TableUnavailable(str(e)[:300]) from e
        raise


_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cached(key: str, fn: Callable[[], Any], ttl: float | None = None) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < (CACHE_TTL if ttl is None else ttl):
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


class DataUnavailable(Exception):
    """A query failed for a reason other than a missing table (SQL error, timeout, warehouse down)."""

    def __init__(self, names: list[str]):
        super().__init__(", ".join(names))
        self.names = names


def _safe(
    fn: Callable[[], Any], missing: list, name: str, default: Any = None, errors: list | None = None
) -> Any:
    """Missing table -> `missing`; any other failure -> `errors` (never silently turned into data)."""
    try:
        return fn()
    except TableUnavailable:
        missing.append(name)
        return default
    except Exception as e:
        log.warning("query %s failed: %s", name, type(e).__name__)
        if errors is None:
            raise DataUnavailable([name]) from e
        errors.append(name)
        return default


# ---------------------------------------------------------------------------
# Data API
# ---------------------------------------------------------------------------
SENSOR_NAMES = {
    "s2": "T24 LPC outlet temperature",
    "s3": "T30 HPC outlet temperature",
    "s4": "T50 LPT outlet temperature",
    "s7": "P30 HPC outlet pressure",
    "s8": "Nf physical fan speed",
    "s9": "Nc physical core speed",
    "s11": "Ps30 HPC outlet static pressure",
    "s12": "phi fuel flow / Ps30 ratio",
    "s13": "NRf corrected fan speed",
    "s14": "NRc corrected core speed",
    "s15": "BPR bypass ratio",
    "s17": "htBleed bleed enthalpy",
    "s20": "W31 HPT coolant bleed",
    "s21": "W32 LPT coolant bleed",
}

ACTION_PREFIX = "Demo estimate; requires engineering review. "


def recommended_action(state: str, rul: float, sensors: Iterable[str] = ()) -> str:
    """Advisory text from state, RUL and top sensors.

    Duplicated from src/lib/cmapss.py because the app is deployed on its own (app/ only);
    tests/test_app_api.py checks that both stay identical.

    It is a statistical estimate from an experimental demo model, not a maintenance order:
    every text says that engineering review is required and none of them instructs a
    shutdown or replacement. The model's uncertainty is not calibrated (no prediction
    intervals); the typical error is only the test RMSE shown in the app.
    """
    state = str(state or "").strip().lower()
    n = round(max(float(rul), 0.0))
    sens = [s for s in sensors if s][:2]
    if state == "critical":
        txt = ACTION_PREFIX + f"Estimated RUL is low (about {n} cycles): consider prioritising an inspection."
        if sens:
            txt += " Sensors to check first: " + " and ".join(sens) + "."
        return txt
    if state == "watch":
        return ACTION_PREFIX + (
            f"Estimated RUL is moderate (about {n} cycles): "
            "consider closer monitoring and planning an inspection."
        )
    return ACTION_PREFIX + "No degradation flagged by the model; keep the usual maintenance plan."


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None and float(v) == float(v) else None
    except (TypeError, ValueError):
        return None


def _valid_rul(v: Any) -> bool:
    try:
        return v is not None and float(v) == float(v)  # not None, not NaN
    except (TypeError, ValueError):
        return False


def _prepare_engine(r: dict) -> dict:
    e = dict(r)
    e["state"] = str(e.get("state") or "").strip().lower()
    if _valid_rul(e.get("rul_pred")):
        e["action"] = recommended_action(
            e["state"], float(e["rul_pred"]), [e.get("sensor_1"), e.get("sensor_2")]
        )
    else:  # no prediction: say so, do not default the RUL to 0
        e["rul_pred"] = None
        e["state"] = "unknown"
        e["action"] = "No prediction available for this engine."
    return e


def q_engines() -> list[dict]:
    return cached(
        "engines",
        lambda: [
            _prepare_engine(r)
            for r in run_query(
                f"SELECT unit, cycle, rul_pred, rul_real, error, state, sensor_1, contrib_1, sensor_2, "
                f"contrib_2, sensor_3, contrib_3, model_version, scored_at, critical_threshold, "
                f"watch_threshold FROM {FQ}.gold_predictions ORDER BY unit"
            )
        ],
    )


def near_failure_stats(engines: list[dict], max_rul: float = NEAR_FAILURE_MAX_RUL) -> dict | None:
    """Bias of the batch predictions on the engines whose actual test RUL is <= max_rul.

    Computed from the scored rows themselves (rul_real comes from RUL_FD001.txt), so the figure
    follows the deployed model version. Positive bias = the model overestimates the remaining life,
    which is the unsafe direction. None when no engine qualifies.
    """
    rows = [
        e
        for e in engines
        if e.get("rul_pred") is not None and _valid_rul(e.get("rul_real")) and float(e["rul_real"]) <= max_rul
    ]
    if not rows:
        return None
    errs = [float(e["rul_pred"]) - float(e["rul_real"]) for e in rows]
    return {
        "max_actual_rul": max_rul,
        "n": len(rows),
        "mean_bias_cycles": round(sum(errs) / len(errs), 1),
        "rmse_cycles": round((sum(x * x for x in errs) / len(errs)) ** 0.5, 1),
        "overestimated": sum(1 for x in errs if x > 0),
    }


def q_metrics(version: int | None) -> dict | None:
    def go():
        if version is not None:
            rows = run_query(
                f"SELECT model_version, rmse_test, phm08_test, trained_at FROM {FQ}.gold_model_metrics "
                f"WHERE model_type = 'hist_gradient_boosting' AND model_version = :v "
                f"ORDER BY trained_at DESC LIMIT 1",
                {"v": int(version)},
            )
            return rows[0] if rows else None  # no fallback to another version's metrics
        rows = run_query(
            f"SELECT model_version, rmse_test, phm08_test, trained_at FROM {FQ}.gold_model_metrics "
            f"WHERE model_type = 'hist_gradient_boosting' ORDER BY trained_at DESC LIMIT 1"
        )
        return rows[0] if rows else None

    return cached(f"metrics:{version}", go)


def q_count(table: str) -> int:
    if not re.fullmatch(r"[a-z_]+", table):
        raise ValueError("table not allowed")
    return cached(f"count:{table}", lambda: int(run_query(f"SELECT COUNT(*) AS n FROM {FQ}.{table}")[0]["n"]))


def q_last_modified(table: str) -> str | None:
    def go():
        # DESCRIBE DETAIL fails on materialized views (gold_*): use the ingestion timestamp instead.
        rows = run_query(f"SELECT MAX(_ingest_ts) AS t FROM {FQ}.{table}")
        return rows[0].get("t") if rows else None

    return cached(f"detail:{table}", go)


def build_overview() -> dict:
    missing: list[str] = []
    errors: list[str] = []
    engines = _safe(q_engines, missing, "gold_predictions", [], errors)
    if errors:  # without predictions there is nothing honest to show: explicit failure
        raise DataUnavailable(errors)
    version = engines[0]["model_version"] if engines else None
    metrics = _safe(lambda: q_metrics(version), missing, "gold_model_metrics", None, errors)
    layers = {
        "bronze": _safe(
            lambda: q_count("bronze_cmapss_readings"), missing, "bronze_cmapss_readings", None, errors
        ),
        "silver": _safe(lambda: q_count("silver_readings"), missing, "silver_readings", None, errors),
        "gold": _safe(lambda: q_count("gold_features"), missing, "gold_features", None, errors),
        "predictions": len(engines) if engines else None,
    }
    updated = _safe(lambda: q_last_modified("gold_features"), missing, "gold_features", None, errors)
    n = len(engines)
    valid = [e for e in engines if e["rul_pred"] is not None]
    critical = sum(1 for e in valid if e["state"] == "critical")
    watch = sum(1 for e in valid if e["state"] == "watch")
    mean_rul = round(sum(float(e["rul_pred"]) for e in valid) / len(valid), 1) if valid else None
    rmse = round(float(metrics["rmse_test"]), 2) if metrics else None
    first = engines[0] if engines else {}
    thresholds = (
        {"critical": _num(first.get("critical_threshold")), "watch": _num(first.get("watch_threshold"))}
        if _num(first.get("critical_threshold")) is not None
        else None
    )
    near = near_failure_stats(valid)
    return {
        "ready": n > 0,
        "missing": sorted(set(missing)),
        "errors": sorted(set(errors)),  # partial failures of auxiliary queries (KPIs show "--")
        "pipeline": {"last_update": updated, "layers": layers},
        "model": {
            "name": MODEL_NAME,
            "alias": "champion",
            "version": version,
            "trained_at": metrics["trained_at"] if metrics else None,
            "scored_at": first.get("scored_at") or None,
            "source": "batch scoring table gold_predictions (no live inference)",
            "source_table": PREDICTIONS_TABLE,
            "thresholds": thresholds,
        },
        "kpis": {
            "engines": n,
            "critical": critical,
            "watch": watch,
            "mean_rul": mean_rul,
            "rmse": rmse,
            "phm08": round(float(metrics["phm08_test"]), 0) if metrics else None,
        },
        "uncertainty": {
            "calibrated": False,
            "typical_error_cycles": rmse,
            "note": (
                "Prediction intervals are not calibrated. The RMSE is the typical error measured on the "
                "FD001 test set (last cycle of 100 engines); the test set also informed model selection, "
                "so it is not an independent estimate. Demo estimate; requires engineering review."
            ),
            "near_failure": near,  # bias on engines with low actual RUL, from the scored rows
        },
    }


def _unavailable(e: DataUnavailable) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "data_unavailable",
            "message": "The data source could not be queried (SQL warehouse error or timeout). "
            "No prediction is shown; try again later.",
            "failed": e.names,
        },
    )


@app.get("/api/overview")
def api_overview():
    try:
        return build_overview()
    except DataUnavailable as e:
        return _unavailable(e)


@app.get("/api/engines")
def api_engines():
    missing: list[str] = []
    try:
        rows = _safe(q_engines, missing, "gold_predictions", [])
    except DataUnavailable as e:
        return _unavailable(e)
    return {
        "ready": bool(rows),
        "missing": missing,
        "engines": rows,
        "sensor_names": SENSOR_NAMES,
        "source_table": PREDICTIONS_TABLE,
        "mode": "batch scoring (no live inference)",
    }


@app.get("/api/engines/{unit}/health")
def api_health(unit: int):
    if not 1 <= unit <= MAX_UNIT:
        raise HTTPException(status_code=422, detail="unit out of range")
    missing: list[str] = []
    try:
        rows = _safe(
            lambda: cached(
                f"health:{unit}",
                lambda: run_query(
                    f"SELECT cycle, health_index FROM {FQ}.gold_health_curve "
                    f"WHERE split = 'test' AND unit = :u ORDER BY cycle",
                    {"u": unit},
                ),
            ),
            missing,
            "gold_health_curve",
            [],
        )
    except DataUnavailable as e:
        return _unavailable(e)
    # Drop null/NaN/Inf readings: a corrupt sensor row must not leak a non-finite
    # value into the JSON response (NaN/Inf are not valid JSON).
    pts = [
        (int(r["cycle"]), float(r["health_index"]))
        for r in rows
        if r["health_index"] is not None and math.isfinite(float(r["health_index"]))
    ]
    if not pts and not missing:
        raise HTTPException(status_code=404, detail=f"no health curve for engine {unit}")
    return {
        "ready": bool(pts),
        "missing": missing,
        "unit": unit,
        "threshold": HEALTH_THRESHOLD,
        "history": pts,
        "projection": project_health(pts, threshold=HEALTH_THRESHOLD),
    }


# ---------------------------------------------------------------------------
# Manuals assistant (RAG, see app/rag.py)
# ---------------------------------------------------------------------------
class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)

    @field_validator("question")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 3:
            raise ValueError("question must contain at least 3 non-blank characters")
        return v


class _SearchError(Exception):
    """AI Search failed."""


class _LLMError(Exception):
    """The chat endpoint failed."""


_clients_lock = threading.Lock()
_clients: tuple | None = None


def get_clients() -> tuple:
    """(llm, search) bound to the app identity; created lazily and cached."""
    global _clients
    with _clients_lock:
        if _clients is None:
            from databricks.sdk import WorkspaceClient

            _clients = rag.databricks_clients(WorkspaceClient(), LLM_ENDPOINT, AI_SEARCH_INDEX)
        return _clients


_kinds_lock = threading.Lock()
_kinds: tuple[float, dict | None] = (0.0, None)


def get_kinds() -> dict | None:
    """{(source, page): kind} from the first chunk of each page, cached for 1 h.
    None if the query fails (retrieval then classifies each chunk by its own text)."""
    global _kinds
    now = time.time()
    with _kinds_lock:
        if _kinds[1] is not None and now - _kinds[0] < KINDS_TTL:
            return _kinds[1]
    try:
        rows = run_query(
            f"SELECT source, page, text FROM {CATALOG}.{SCHEMA}.manual_chunks WHERE chunk_idx = 0"
        )
        kinds = rag.page_kind_map(rows)
    except Exception as e:
        log.warning("page kinds unavailable: %s", str(e)[:200])
        return None
    with _kinds_lock:
        _kinds = (now, kinds)
    return kinds


def _guard(fn: Callable, exc: type) -> Callable:
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:
            raise exc(str(e)) from e

    return wrapped


def _claim(c: dict, with_reason: bool = False) -> dict:
    d = {
        "text": c.get("text", ""),
        "citations": [int(x) for x in c.get("citations") or []],
        "verdict": c.get("verdict"),
    }
    if with_reason:
        d["reason"] = c.get("reason", "")
    if c.get("missing_qualifiers"):  # conditions stated by the source that the claim dropped
        d["missing_qualifiers"] = [str(x) for x in c["missing_qualifiers"]]
    return d


def answer_to_json(a: rag.Answer) -> dict:
    claims = [_claim(c) for c in a.claims]
    removed = [_claim(c, True) for c in a.removed_claims]
    shown = {n for c in claims for n in c["citations"]}
    sources = [
        {
            "n": i,
            "source": c.get("source"),
            "page": c.get("page"),
            "score": c.get("score"),
            "rrf": c.get("rrf"),
            "kind": c.get("kind"),
            "snippet": (c.get("text") or "")[:SNIPPET_CHARS],
            "cited": i in shown,
        }
        for i, c in enumerate(a.chunks, 1)
    ]
    return {
        "status": a.status,
        "summary": a.summary,
        "claims": claims,
        "removed_claims": removed,
        "gaps": a.gaps,
        "manufacturer_note": a.manufacturer_note,
        "sources": sources,
        "trace": {
            "config": a.config,
            "queries": a.queries,
            "retrieval": a.retrieval_trace,
            "coverage": a.coverage,
            "adjustments": a.adjustments,
        },
    }


@app.post("/api/ask")
def api_ask(body: Question):
    q = body.question.strip()
    try:
        llm, search = get_clients()
        kinds = get_kinds()
        a = rag.answer(q, _guard(llm, _LLMError), _guard(search, _SearchError), RAG_CONFIG, kinds=kinds)
    except _LLMError as e:
        log.warning("LLM unavailable: %s", str(e)[:300])
        return JSONResponse(
            status_code=502,
            content={
                "error": "llm_unavailable",
                "message": f"Could not query the model '{LLM_ENDPOINT}'. Check LLM_ENDPOINT in app.yaml.",
                "sources": [],
            },
        )
    except Exception as e:  # AI Search or client setup failed
        log.warning("AI Search unavailable: %s", str(e)[:300])
        return JSONResponse(
            status_code=503,
            content={
                "error": "index_unavailable",
                "message": (
                    "The manuals index is not available yet. Upload the PDFs to the 'manuals' "
                    "volume and run the RAG job (04_rag_index)."
                ),
            },
        )
    out = answer_to_json(a)
    log.info(
        "ask config=%s status=%s claims=%d removed=%d pages=%s",
        a.config,
        a.status,
        len(out["claims"]),
        len(out["removed_claims"]),
        [f"{s['source']}:p{s['page']}" for s in out["sources"]],
    )
    return out


# ---------------------------------------------------------------------------
# Service availability (light checks, cached; never raises)
# ---------------------------------------------------------------------------
HEALTH_TTL = float(os.getenv("HEALTH_TTL_SECONDS", "60"))
HEALTH_BUDGET = float(os.getenv("HEALTH_TIMEOUT_SECONDS", "20"))  # per check, seconds


def check_sql() -> dict:
    """SQL warehouse reachable with the app identity (cheap statement, no table scan)."""
    if not WAREHOUSE_ID:
        return {"status": "error", "detail": "DATABRICKS_WAREHOUSE_ID is not set"}
    rows = run_query("SELECT 1 AS ok")
    ok = bool(rows) and int(rows[0].get("ok", 0)) == 1
    return {"status": "ok" if ok else "error", "detail": f"warehouse {WAREHOUSE_ID}"}


def check_index() -> dict:
    """AI Search index exists and reports its status (metadata call, no query)."""
    from databricks.sdk import WorkspaceClient

    idx = WorkspaceClient().vector_search_indexes.get_index(AI_SEARCH_INDEX)
    st = getattr(idx, "status", None)
    ready = getattr(st, "ready", None)
    msg = getattr(st, "message", None) or getattr(st, "detailed_state", None)
    detail = f"{AI_SEARCH_INDEX}" + (f" | {msg}" if msg else "")
    if ready is None:
        return {"status": "ok", "detail": detail + " | index found (readiness not reported)"}
    return {"status": "ok" if ready else "error", "detail": detail}


def check_llm() -> dict:
    """Chat endpoint exists and is READY (metadata call, no tokens consumed)."""
    from databricks.sdk import WorkspaceClient

    ep = WorkspaceClient().serving_endpoints.get(LLM_ENDPOINT)
    state = getattr(getattr(ep, "state", None), "ready", None)
    val = getattr(state, "value", state)
    ok = val is None or str(val).upper() == "READY"
    return {"status": "ok" if ok else "error", "detail": f"{LLM_ENDPOINT} | {val or 'state not reported'}"}


def _run_check(name: str, fn: Callable[[], dict]) -> dict:
    """Runs one check with a time budget; failures become {"status": "error"|"unknown"}."""
    ex = cf.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn)
    try:
        out = fut.result(timeout=HEALTH_BUDGET)
    except cf.TimeoutError:
        out = {"status": "error", "detail": f"no answer within {HEALTH_BUDGET:.0f} s"}
    except TableUnavailable as e:
        out = {"status": "error", "detail": str(e)[:120]}
    except Exception as e:  # permission or client errors: do not pretend the service is down
        kind = type(e).__name__
        out = {
            "status": "unknown"
            if "Permission" in kind or "NotFound" in kind or "Forbidden" in kind
            else "error",
            "detail": kind,
        }
    finally:
        ex.shutdown(wait=False)
    log.info("health %s: %s", name, out["status"])
    return {"name": name, **out}


CHECKS: dict[str, Callable[[], dict]] = {
    "sql_warehouse": check_sql,
    "search_index": check_index,
    "llm": check_llm,
}


def build_health() -> dict:
    services = {k: _run_check(k, fn) for k, fn in CHECKS.items()}
    overall = (
        "ok"
        if all(v["status"] == "ok" for v in services.values())
        else "degraded"
        if any(v["status"] == "ok" for v in services.values())
        else "down"
    )
    return {
        "status": overall,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "ttl_seconds": HEALTH_TTL,
        "services": services,
    }


@app.get("/api/health")
def api_service_health():
    """Availability of the three services the dashboard depends on. Always HTTP 200 with the detail;
    the overall status is "ok", "degraded" or "down"."""
    return cached("health", build_health, HEALTH_TTL) if HEALTH_TTL > 0 else build_health()


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(os.path.join(_STATIC, "index.html"), headers={"Cache-Control": "no-cache"})
