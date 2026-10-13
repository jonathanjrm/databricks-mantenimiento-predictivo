"""Backend of the "Predictive turbine maintenance" app (Databricks Apps, FastAPI).

- Data: databricks-sql-connector against the SQL warehouse declared as an app resource,
  using the app identity (service principal) through the SDK Config().
- Assistant: app/rag.py (multi-query retrieval from AI Search, structured answer, claim
  verification) with a Foundation Model APIs LLM.
- If a table does not exist yet, the endpoints return an explicit empty state (ready=false),
  not a 500 error.
"""
from __future__ import annotations

import datetime as dt
import decimal
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import rag
from health_projection import project_health

log = logging.getLogger("maintenance")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------------------------------
# Configuracion (variables de entorno definidas en app.yaml)
# ---------------------------------------------------------------------------
_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
CATALOG = os.getenv("CATALOG", "workspace")
SCHEMA = os.getenv("SCHEMA", "mantenimiento")
for _v in (CATALOG, SCHEMA):
    if not _IDENT.fullmatch(_v):
        raise RuntimeError(f"Invalid identifier in the configuration: {_v!r}")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
WAREHOUSE_ID = os.getenv("DATABRICKS_WAREHOUSE_ID", "")
AI_SEARCH_INDEX = os.getenv("AI_SEARCH_INDEX", f"{CATALOG}.{SCHEMA}.manual_chunks_index")
# Endpoint de chat de Foundation Model APIs. verificar disponibilidad en Free Edition
# (los modelos alojados cambian y se retiran; revisa la pestana Serving).
LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "databricks-gpt-oss-120b")
RAG_CONFIG = os.getenv("RAG_CONFIG", "v2")
KINDS_TTL = 3600.0
CACHE_TTL = float(os.getenv("CACHE_TTL_SECONDS", "30"))
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"

app = FastAPI(title="Predictive turbine maintenance", docs_url=None, redoc_url=None)


class TableUnavailable(Exception):
    """The queried table or view does not exist yet."""


# ---------------------------------------------------------------------------
# Acceso a datos
# ---------------------------------------------------------------------------
_cfg_lock = threading.Lock()
_cfg = None


def _config():
    global _cfg
    with _cfg_lock:
        if _cfg is None:
            from databricks.sdk.core import Config
            _cfg = Config()  # en Apps toma DATABRICKS_HOST, DATABRICKS_CLIENT_ID y DATABRICKS_CLIENT_SECRET
        return _cfg


def _jsonable(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def _is_missing_table(msg: str) -> bool:
    m = msg.upper()
    return any(t in m for t in ("TABLE_OR_VIEW_NOT_FOUND", "SCHEMA_NOT_FOUND", "CATALOG_NOT_FOUND",
                                "DELTA_TABLE_NOT_FOUND", "DOES NOT EXIST", "CANNOT BE FOUND"))


def run_query(sql_text: str, params: Optional[dict] = None) -> list[dict]:
    """Runs SQL on the warehouse and returns rows as dictionaries."""
    if not WAREHOUSE_ID:
        raise TableUnavailable("DATABRICKS_WAREHOUSE_ID is not set (sql_warehouse app resource)")
    from databricks import sql
    cfg = _config()
    host = re.sub(r"^https?://", "", cfg.host).rstrip("/")
    try:
        with sql.connect(server_hostname=host,
                         http_path=f"/sql/1.0/warehouses/{WAREHOUSE_ID}",
                         credentials_provider=lambda: cfg.authenticate) as conn:
            with conn.cursor() as cur:
                cur.execute(sql_text, params or {})
                cols = [d[0] for d in cur.description]
                return [{c: _jsonable(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    except Exception as e:  # el conector lanza excepciones propias; se distingue por el mensaje
        if _is_missing_table(str(e)):
            raise TableUnavailable(str(e)[:300]) from e
        raise


_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cached(key: str, fn: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def _safe(fn: Callable[[], Any], missing: list, name: str, default: Any = None) -> Any:
    try:
        return fn()
    except TableUnavailable:
        missing.append(name)
        return default
    except Exception as e:
        log.warning("query %s failed: %s", name, str(e)[:300])
        missing.append(name)
        return default


# ---------------------------------------------------------------------------
# API de datos
# ---------------------------------------------------------------------------
SENSOR_NAMES = {
    "s2": "T24 LPC outlet temperature", "s3": "T30 HPC outlet temperature",
    "s4": "T50 LPT outlet temperature", "s7": "P30 HPC outlet pressure",
    "s8": "Nf physical fan speed", "s9": "Nc physical core speed",
    "s11": "Ps30 HPC outlet static pressure", "s12": "phi fuel flow / Ps30 ratio",
    "s13": "NRf corrected fan speed", "s14": "NRc corrected core speed",
    "s15": "BPR bypass ratio", "s17": "htBleed bleed enthalpy",
    "s20": "W31 HPT coolant bleed", "s21": "W32 LPT coolant bleed",
}

_LEGACY_STATES = {"critico": "critical", "vigilar": "watch", "sano": "healthy"}


def normalize_state(state: Any) -> str:
    """Accepts both legacy Spanish (critico, vigilar, sano) and English state values."""
    st = str(state or "").strip().lower()
    return _LEGACY_STATES.get(st, st)


def recommended_action(state: str, rul: float, sensors: list) -> str:
    """Generic action text from state, RUL and top sensors (same wording as src/lib/cmapss.py).
    Always recomputed here so the UI is in English even for rows scored before the change."""
    n = int(round(max(float(rul), 0.0)))
    sens = [x for x in sensors if x][:2]
    if state == "critical":
        txt = f"Schedule shutdown and replacement within {n} cycles."
        if sens:
            txt += " Inspect " + " and ".join(sens) + " now."
        return txt
    if state == "watch":
        return f"Increase inspection frequency and plan the intervention within the {n}-cycle window."
    return "Normal operation. Keep the usual maintenance plan."


def _prepare_engine(r: dict) -> dict:
    e = dict(r)
    e["estado"] = normalize_state(e.get("estado"))
    e["action"] = recommended_action(e["estado"], e.get("rul_pred") or 0.0,
                                     [e.get("sensor_1"), e.get("sensor_2")])
    e.pop("accion", None)
    return e


def q_engines() -> list[dict]:
    return cached("engines", lambda: [_prepare_engine(r) for r in run_query(
        f"SELECT unit, cycle, rul_pred, rul_real, error, estado, sensor_1, contrib_1, sensor_2, contrib_2, "
        f"sensor_3, contrib_3, model_version, scored_at FROM {FQ}.gold_predicciones ORDER BY unit")])


def q_metrics(version: Optional[int]) -> Optional[dict]:
    def go():
        if version is not None:
            rows = run_query(
                f"SELECT model_version, rmse_test, phm08_test, trained_at FROM {FQ}.gold_model_metrics "
                f"WHERE model_type = 'hist_gradient_boosting' AND model_version = :v "
                f"ORDER BY trained_at DESC LIMIT 1", {"v": int(version)})
            if rows:
                return rows[0]
        rows = run_query(
            f"SELECT model_version, rmse_test, phm08_test, trained_at FROM {FQ}.gold_model_metrics "
            f"WHERE model_type = 'hist_gradient_boosting' ORDER BY trained_at DESC LIMIT 1")
        return rows[0] if rows else None
    return cached(f"metrics:{version}", go)


def q_count(table: str) -> int:
    if not re.fullmatch(r"[a-z_]+", table):
        raise ValueError("table not allowed")
    return cached(f"count:{table}", lambda: int(run_query(f"SELECT COUNT(*) AS n FROM {FQ}.{table}")[0]["n"]))


def q_last_modified(table: str) -> Optional[str]:
    def go():
        rows = run_query(f"DESCRIBE DETAIL {FQ}.{table}")
        return rows[0].get("lastModified") if rows else None
    return cached(f"detail:{table}", go)


def build_overview() -> dict:
    missing: list[str] = []
    engines = _safe(q_engines, missing, "gold_predicciones", [])
    version = engines[0]["model_version"] if engines else None
    metrics = _safe(lambda: q_metrics(version), missing, "gold_model_metrics")
    layers = {
        "bronze": _safe(lambda: q_count("bronze_cmapss_lecturas"), missing, "bronze_cmapss_lecturas"),
        "silver": _safe(lambda: q_count("silver_lecturas"), missing, "silver_lecturas"),
        "gold": _safe(lambda: q_count("gold_features"), missing, "gold_features"),
        "predictions": len(engines) if engines else None,
    }
    updated = _safe(lambda: q_last_modified("gold_features"), missing, "gold_features")
    n = len(engines)
    critical = sum(1 for e in engines if e["estado"] == "critical")
    watch = sum(1 for e in engines if e["estado"] == "watch")
    mean_rul = round(sum(float(e["rul_pred"]) for e in engines) / n, 1) if n else None
    return {
        "ready": n > 0,
        "missing": sorted(set(missing)),
        "pipeline": {"last_update": updated, "layers": layers},
        "model": {"name": MODEL_NAME, "alias": "champion", "version": version,
                  "trained_at": metrics["trained_at"] if metrics else None},
        "kpis": {"engines": n, "critical": critical, "watch": watch, "mean_rul": mean_rul,
                 "rmse": round(float(metrics["rmse_test"]), 2) if metrics else None,
                 "phm08": round(float(metrics["phm08_test"]), 0) if metrics else None},
    }


@app.get("/api/overview")
def api_overview():
    return build_overview()


@app.get("/api/engines")
def api_engines():
    missing: list[str] = []
    rows = _safe(q_engines, missing, "gold_predicciones", [])
    return {"ready": bool(rows), "missing": missing, "engines": rows, "sensor_names": SENSOR_NAMES}


@app.get("/api/engines/{unit}/health")
def api_health(unit: int):
    if not 1 <= unit <= 10_000:
        raise HTTPException(status_code=422, detail="unit out of range")
    missing: list[str] = []
    rows = _safe(lambda: cached(f"health:{unit}", lambda: run_query(
        f"SELECT cycle, health_index FROM {FQ}.gold_salud_curva WHERE split = 'test' AND unit = :u "
        f"ORDER BY cycle", {"u": unit})), missing, "gold_salud_curva", [])
    pts = [(int(r["cycle"]), float(r["health_index"])) for r in rows if r["health_index"] is not None]
    return {"ready": bool(pts), "missing": missing, "unit": unit, "threshold": 1.0,
            "history": pts, "projection": project_health(pts, threshold=1.0)}


# ---------------------------------------------------------------------------
# Manuals assistant (RAG, see app/rag.py)
# ---------------------------------------------------------------------------
class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class _SearchError(Exception):
    """AI Search failed."""


class _LLMError(Exception):
    """The chat endpoint failed."""


_clients_lock = threading.Lock()
_clients: Optional[tuple] = None


def get_clients() -> tuple:
    """(llm, search) bound to the app identity; created lazily and cached."""
    global _clients
    with _clients_lock:
        if _clients is None:
            from databricks.sdk import WorkspaceClient
            _clients = rag.databricks_clients(WorkspaceClient(), LLM_ENDPOINT, AI_SEARCH_INDEX)
        return _clients


_kinds_lock = threading.Lock()
_kinds: tuple[float, Optional[dict]] = (0.0, None)


def get_kinds() -> Optional[dict]:
    """{(source, page): kind} from the first chunk of each page, cached for 1 h.
    None if the query fails (retrieval then classifies each chunk by its own text)."""
    global _kinds
    now = time.time()
    with _kinds_lock:
        if _kinds[1] is not None and now - _kinds[0] < KINDS_TTL:
            return _kinds[1]
    try:
        rows = run_query(f"SELECT source, page, text FROM {CATALOG}.{SCHEMA}.manual_chunks WHERE chunk_idx = 0")
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
    d = {"text": c.get("text", ""), "citations": [int(x) for x in c.get("citations") or []],
         "verdict": c.get("verdict")}
    if with_reason:
        d["reason"] = c.get("reason", "")
    return d


def answer_to_json(a: Any) -> dict:
    claims = [_claim(c) for c in a.claims]
    removed = [_claim(c, True) for c in a.removed_claims]
    shown = {n for c in claims for n in c["citations"]}
    sources = [{"n": i, "source": c.get("source"), "page": c.get("page"), "score": c.get("score"),
                "rrf": c.get("rrf"), "kind": c.get("kind"), "snippet": (c.get("text") or "")[:300],
                "cited": i in shown} for i, c in enumerate(a.chunks, 1)]
    return {"status": a.status, "summary": a.summary, "claims": claims, "removed_claims": removed,
            "gaps": a.gaps, "manufacturer_note": a.manufacturer_note, "sources": sources,
            "trace": {"config": a.config, "queries": a.queries, "retrieval": a.retrieval_trace,
                      "coverage": a.coverage, "adjustments": a.adjustments}}


@app.post("/api/ask")
def api_ask(body: Question):
    q = body.question.strip()
    try:
        llm, search = get_clients()
        kinds = get_kinds()
        a = rag.answer(q, _guard(llm, _LLMError), _guard(search, _SearchError), RAG_CONFIG, kinds=kinds)
    except _LLMError as e:
        log.warning("LLM unavailable: %s", str(e)[:300])
        return JSONResponse(status_code=502, content={
            "error": "llm_unavailable",
            "message": f"Could not query the model '{LLM_ENDPOINT}'. Check LLM_ENDPOINT in app.yaml.",
            "sources": []})
    except Exception as e:  # AI Search or client setup failed
        log.warning("AI Search unavailable: %s", str(e)[:300])
        return JSONResponse(status_code=503, content={
            "error": "index_unavailable",
            "message": ("The manuals index is not available yet. Upload the PDFs to the 'manuales' "
                        "volume and run the RAG job (04_rag_index).")})
    out = answer_to_json(a)
    log.info("ask config=%s status=%s claims=%d removed=%d pages=%s", a.config, a.status,
             len(out["claims"]), len(out["removed_claims"]),
             [f"{s['source']}:p{s['page']}" for s in out["sources"]])
    return out


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
