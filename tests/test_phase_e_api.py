"""Phase E (interface) API additions: service health, thresholds and traceability, near-failure bias,
omitted conditions in assistant claims. All SQL and Databricks clients are simulated."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import app as A  # noqa: E402
import rag  # noqa: E402

ROW = {"unit": 1, "cycle": 100, "rul_pred": 10.0, "rul_real": 7, "error": 3.0, "estado": "critical",
       "sensor_1": "s4", "contrib_1": 5.0, "sensor_2": "s11", "contrib_2": 3.0, "sensor_3": "s2",
       "contrib_3": 1.0, "model_version": 1, "scored_at": "2026-10-13T20:26:50", "umbral_critico": 20.0,
       "umbral_vigilar": 35.0}


@pytest.fixture(autouse=True)
def limpiar():
    A._cache.clear()
    yield
    A._cache.clear()


def _rows():
    return [ROW, dict(ROW, unit=2, rul_pred=28.0, rul_real=30, error=-2.0, estado="watch"),
            dict(ROW, unit=3, rul_pred=90.0, rul_real=95, error=-5.0, estado="healthy"),
            dict(ROW, unit=4, rul_pred=None, rul_real=20, error=None, estado="critical")]


def _q(sql, params=None):
    if "gold_predicciones" in sql:
        return _rows()
    if "gold_model_metrics" in sql:
        return [{"model_version": 1, "rmse_test": 18.44, "phm08_test": 655.0, "trained_at": "2026-10-13T18:46:00"}]
    if "COUNT" in sql:
        return [{"n": 100}]
    if "SELECT 1" in sql:
        return [{"ok": 1}]
    return [{"t": "2026-10-13T10:00:00+00:00"}]


def test_engines_llevan_umbrales_y_tabla_origen(monkeypatch):
    monkeypatch.setattr(A, "run_query", _q)
    j = TestClient(A.app).get("/api/engines").json()
    assert j["source_table"] == "workspace.mantenimiento.gold_predicciones" and "no live inference" in j["mode"]
    assert j["engines"][0]["umbral_critico"] == 20.0 and j["engines"][0]["scored_at"].startswith("2026-10-13")


def test_overview_umbrales_trazabilidad_y_sesgo_cerca_del_fallo(monkeypatch):
    monkeypatch.setattr(A, "run_query", _q)
    j = TestClient(A.app).get("/api/overview").json()
    assert j["model"]["thresholds"] == {"critical": 20.0, "watch": 35.0}
    assert j["model"]["source_table"].endswith("gold_predicciones") and j["model"]["scored_at"].startswith("2026-10-13T20")
    nf = j["uncertainty"]["near_failure"]
    # engines with actual RUL <= 30 and a prediction: unit 1 (+3) and unit 2 (-2); unit 4 has no prediction
    assert nf["n"] == 2 and nf["mean_bias_cycles"] == 0.5 and nf["overestimated"] == 1
    assert nf["rmse_cycles"] == pytest.approx(((9 + 4) / 2) ** 0.5, abs=0.05)
    assert j["uncertainty"]["calibrated"] is False


def test_sesgo_cerca_del_fallo_es_none_sin_motores():
    assert A.near_failure_stats([]) is None
    assert A.near_failure_stats([dict(ROW, rul_real=90)]) is None
    assert A.near_failure_stats([dict(ROW, rul_real=None)]) is None


def test_overview_sin_umbrales_devuelve_null(monkeypatch):
    rows = [{k: v for k, v in ROW.items() if not k.startswith("umbral")}]
    monkeypatch.setattr(A, "run_query", lambda s, p=None: rows if "gold_predicciones" in s else _q(s, p))
    j = TestClient(A.app).get("/api/overview").json()
    assert j["model"]["thresholds"] is None and j["ready"] is True


def test_health_todo_ok(monkeypatch):
    monkeypatch.setattr(A, "CHECKS", {"sql_warehouse": lambda: {"status": "ok", "detail": "w"},
                                      "search_index": lambda: {"status": "ok", "detail": "i"},
                                      "llm": lambda: {"status": "ok", "detail": "l"}})
    r = TestClient(A.app).get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok" and set(j["services"]) == {"sql_warehouse", "search_index", "llm"}
    assert j["services"]["llm"] == {"name": "llm", "status": "ok", "detail": "l"} and j["checked_at"]


def test_health_degradado_y_caido_nunca_lanza(monkeypatch):
    def boom():
        raise RuntimeError("endpoint down")

    def forbidden():
        raise PermissionError("no access")
    monkeypatch.setattr(A, "CHECKS", {"sql_warehouse": lambda: {"status": "ok", "detail": "w"},
                                      "search_index": boom, "llm": forbidden})
    j = TestClient(A.app).get("/api/health").json()
    assert j["status"] == "degraded"
    assert j["services"]["search_index"]["status"] == "error" and j["services"]["search_index"]["detail"] == "RuntimeError"
    assert j["services"]["llm"]["status"] == "unknown"      # permission problems are not reported as outages
    A._cache.clear()
    monkeypatch.setattr(A, "CHECKS", {"sql_warehouse": boom, "search_index": boom, "llm": boom})
    j = TestClient(A.app).get("/api/health").json()
    assert j["status"] == "down" and all(v["status"] == "error" for v in j["services"].values())


def test_health_respeta_presupuesto_de_tiempo(monkeypatch):
    import time

    def slow():
        time.sleep(2)
        return {"status": "ok"}
    monkeypatch.setattr(A, "HEALTH_BUDGET", 0.2)
    monkeypatch.setattr(A, "CHECKS", {"sql_warehouse": slow})
    t0 = time.time()
    j = TestClient(A.app).get("/api/health").json()
    assert time.time() - t0 < 1.5 and j["services"]["sql_warehouse"]["status"] == "error"
    assert "within" in j["services"]["sql_warehouse"]["detail"]


def test_health_se_cachea(monkeypatch):
    calls = []
    monkeypatch.setattr(A, "CHECKS", {"sql_warehouse": lambda: calls.append(1) or {"status": "ok"}})
    c = TestClient(A.app)
    c.get("/api/health"); c.get("/api/health")
    assert len(calls) == 1


def test_check_sql_usa_consulta_barata(monkeypatch):
    seen = []
    monkeypatch.setattr(A, "run_query", lambda s, p=None: seen.append(s) or [{"ok": 1}])
    monkeypatch.setattr(A, "WAREHOUSE_ID", "wh")
    assert A.check_sql()["status"] == "ok" and seen == ["SELECT 1 AS ok"]
    monkeypatch.setattr(A, "WAREHOUSE_ID", "")
    assert A.check_sql()["status"] == "error"


def test_claims_exponen_condiciones_omitidas(monkeypatch):
    a = rag.Answer(config="v2", question="q", queries=["x"], retrieval_trace=[], chunks=[
        {"chunk_id": "a", "source": "faa.pdf", "page": 413, "text": "On centrifugal flow engines, remove the screen.",
         "score": 0.9, "kind": "body"}], status="insufficient_evidence", summary="s",
        claims=[{"text": "Remove the inlet screen.", "citations": [1], "verdict": "PARTIAL",
                 "reason": "deterministic check", "missing_qualifiers": ["centrifugal flow engines"]},
                {"text": "Other.", "citations": [1], "verdict": "SUPPORTED"}])
    monkeypatch.setattr(A, "get_clients", lambda: (lambda m, t=800: "", lambda q, n: []))
    monkeypatch.setattr(A, "get_kinds", lambda: None)
    monkeypatch.setattr(A.rag, "answer", lambda *a, **k: a_)
    a_ = a
    j = TestClient(A.app).post("/api/ask", json={"question": "how is the compressor inspected?"}).json()
    assert j["claims"][0]["missing_qualifiers"] == ["centrifugal flow engines"]
    assert "missing_qualifiers" not in j["claims"][1]


def test_pagina_tiene_filtros_estados_y_accesibilidad():
    html = TestClient(A.app).get("/").text
    for needle in ('id="filters"', 'id="banner"', 'role="alert"', '/api/health', 'label for="q"',
                   'condition omitted', 'model prediction', 'test ground truth', 'illustrative', 'Traceability'):
        assert needle in html, needle
    assert "—" not in html   # no em dashes
