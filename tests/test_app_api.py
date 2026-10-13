import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import app as A  # noqa: E402
from health_projection import project_health  # noqa: E402

ROWS = [
    {"unit": 1, "cycle": 100, "rul_pred": 10.0, "rul_real": 12, "error": -2.0, "estado": "critico",
     "sensor_1": "s4", "contrib_1": 5.0, "sensor_2": "s11", "contrib_2": 3.0, "sensor_3": "s2",
     "contrib_3": 1.0, "model_version": 3, "scored_at": "2026-10-13T00:00:00"},
    {"unit": 2, "cycle": 90, "rul_pred": 30.0, "rul_real": 28, "error": 2.0, "estado": "vigilar",
     "sensor_1": "s4", "contrib_1": 5.0, "sensor_2": "s11", "contrib_2": 3.0, "sensor_3": "s2",
     "contrib_3": 1.0, "model_version": 3, "scored_at": "2026-10-13T00:00:00"},
    {"unit": 3, "cycle": 80, "rul_pred": 90.0, "rul_real": 95, "error": -5.0, "estado": "sano",
     "sensor_1": "s4", "contrib_1": 5.0, "sensor_2": "s11", "contrib_2": 3.0, "sensor_3": "s2",
     "contrib_3": 1.0, "model_version": 3, "scored_at": "2026-10-13T00:00:00"},
]


@pytest.fixture(autouse=True)
def limpiar_cache():
    A._cache.clear()
    yield
    A._cache.clear()


def _fake(sql, params=None):
    s = " ".join(sql.split())
    if "gold_predicciones" in s:
        return ROWS
    if "gold_model_metrics" in s:
        return [{"model_version": 3, "rmse_test": 18.5, "phm08_test": 640.0, "trained_at": "2026-10-13"}]
    if "COUNT(*)" in s:
        return [{"n": 33727}]
    if "DESCRIBE DETAIL" in s:
        return [{"lastModified": "2026-10-13T10:00:00+00:00"}]
    if "gold_salud_curva" in s:
        return [{"cycle": c, "health_index": 0.01 * c} for c in range(1, 61)]
    return []


def test_overview_con_datos(monkeypatch):
    monkeypatch.setattr(A, "run_query", _fake)
    j = TestClient(A.app).get("/api/overview").json()
    assert j["ready"] and j["kpis"]["motores"] == 3 and j["kpis"]["criticos"] == 1
    assert j["kpis"]["rul_media"] == pytest.approx(43.3, abs=0.1)
    assert j["kpis"]["rmse"] == 18.5 and j["model"]["version"] == 3


def test_estado_vacio_si_faltan_tablas(monkeypatch):
    def vacio(sql, params=None):
        raise A.TablaNoDisponible("TABLE_OR_VIEW_NOT_FOUND")
    monkeypatch.setattr(A, "run_query", vacio)
    c = TestClient(A.app)
    o = c.get("/api/overview")
    assert o.status_code == 200 and o.json()["ready"] is False and o.json()["missing"]
    e = c.get("/api/engines")
    assert e.status_code == 200 and e.json()["engines"] == []
    h = c.get("/api/engines/1/health")
    assert h.status_code == 200 and h.json()["ready"] is False


def test_curva_de_salud_y_proyeccion(monkeypatch):
    monkeypatch.setattr(A, "run_query", _fake)
    j = TestClient(A.app).get("/api/engines/1/health").json()
    assert j["ready"] and len(j["history"]) == 60
    # indicador = 0.01 * ciclo: cruza 1.0 en el ciclo 100 -> faltan 40
    assert j["projection"]["cycles_to_threshold"] == 40 and j["projection"]["cross_cycle"] == 100
    assert TestClient(A.app).get("/api/engines/0/health").status_code == 422


def test_proyeccion_sin_tendencia():
    pts = [(c, 0.2 - 0.001 * c) for c in range(1, 40)]
    p = project_health(pts)
    assert p["cycles_to_threshold"] is None and p["projection"] == []
    assert project_health([(1, 0.1)])["slope"] is None


def test_ask_cita_fuentes(monkeypatch):
    chunks = [{"chunk_id": "a", "source": "manual.pdf", "page": 7, "text": "Revisar cada 500 horas.", "score": 0.9},
              {"chunk_id": "b", "source": "otro.pdf", "page": 2, "text": "Texto no relacionado.", "score": 0.4}]
    monkeypatch.setattr(A, "retrieve", lambda q, k=4: chunks)
    seen = {}

    def llm(messages):
        seen["m"] = messages
        return "Cada 500 horas [1]."
    monkeypatch.setattr(A, "call_llm", llm)
    j = TestClient(A.app).post("/api/ask", json={"question": "cada cuanto reviso?"}).json()
    assert j["answer"] == "Cada 500 horas [1]."
    assert [s["cited"] for s in j["sources"]] == [True, False]
    assert j["sources"][0]["source"] == "manual.pdf" and j["sources"][0]["page"] == 7
    assert "[1] (manual.pdf, p. 7)" in seen["m"][1]["content"]


def test_ask_errores_claros(monkeypatch):
    def boom(q, k=4):
        raise RuntimeError("index not found")
    monkeypatch.setattr(A, "retrieve", boom)
    r = TestClient(A.app).post("/api/ask", json={"question": "hola mundo"})
    assert r.status_code == 503 and r.json()["error"] == "indice_no_disponible"
    monkeypatch.setattr(A, "retrieve", lambda q, k=4: [])
    assert "No encuentro" in TestClient(A.app).post("/api/ask", json={"question": "hola mundo"}).json()["answer"]
    assert TestClient(A.app).post("/api/ask", json={"question": "a"}).status_code == 422


def test_extract_text_acepta_bloques():
    resp = {"choices": [{"message": {"content": [{"type": "reasoning", "text": "pienso"},
                                                  {"type": "text", "text": "respuesta"}]}}]}
    assert A.extract_text(resp) == "respuesta"
    assert A.extract_text({"choices": [{"message": {"content": " hola "}}]}) == "hola"


def test_pagina_principal():
    r = TestClient(A.app).get("/")
    assert r.status_code == 200 and "Mantenimiento predictivo" in r.text
