"""Pruebas negativas de la API (auditoria B4): ante fallos la app informa y NO inventa predicciones.

La app no llama al endpoint de serving: lee la tabla de scoring por lotes (gold_predicciones).
Estas pruebas simulan fallos de SQL, timeout, tabla ausente, warehouse sin configurar y filas incompletas.
"""
import inspect
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import app as A  # noqa: E402

GOOD = {"unit": 1, "cycle": 100, "rul_pred": 10.0, "rul_real": 12, "error": -2.0, "estado": "critical",
        "sensor_1": "s4", "contrib_1": 5.0, "sensor_2": "s11", "contrib_2": 3.0, "sensor_3": "s2",
        "contrib_3": 1.0, "model_version": 1, "scored_at": "2026-10-13T00:00:00"}


@pytest.fixture(autouse=True)
def limpiar():
    A._cache.clear()
    yield
    A._cache.clear()


def _client():
    return TestClient(A.app, raise_server_exceptions=False)


def _boom(exc):
    def f(sql, params=None):
        raise exc
    return f


@pytest.mark.parametrize("exc", [RuntimeError("SQL syntax error"), TimeoutError("socket timeout"),
                                 ConnectionError("warehouse unreachable")])
def test_fallo_de_sql_es_503_explicito_sin_datos_inventados(monkeypatch, exc):
    monkeypatch.setattr(A, "run_query", _boom(exc))
    c = _client()
    for url in ("/api/engines", "/api/overview", "/api/engines/1/health"):
        r = c.get(url)
        assert r.status_code == 503, url
        j = r.json()
        assert j["error"] == "data_unavailable" and "No prediction" in j["message"]
        assert "engines" not in j and "kpis" not in j and "history" not in j


def test_tabla_ausente_es_estado_vacio_explicito(monkeypatch):
    monkeypatch.setattr(A, "run_query", _boom(A.TableUnavailable("TABLE_OR_VIEW_NOT_FOUND")))
    j = _client().get("/api/engines").json()
    assert j["ready"] is False and j["engines"] == [] and j["missing"] == ["gold_predicciones"]


def test_warehouse_no_configurado_es_estado_vacio_no_prediccion(monkeypatch):
    monkeypatch.setattr(A, "WAREHOUSE_ID", "")
    j = _client().get("/api/engines").json()
    assert j["ready"] is False and j["engines"] == []


def test_run_query_traduce_tabla_inexistente_y_propaga_otros_errores(monkeypatch):
    import types, sys
    class FakeConn:
        def __init__(self, msg): self.msg = msg
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return self
        def execute(self, *a, **k): raise RuntimeError(self.msg)
    fake_sql = types.SimpleNamespace(connect=lambda **k: FakeConn(fake_sql.msg))
    pkg = types.ModuleType("databricks"); pkg.sql = fake_sql
    monkeypatch.setitem(sys.modules, "databricks", pkg); monkeypatch.setitem(sys.modules, "databricks.sql", fake_sql)
    monkeypatch.setattr(A, "WAREHOUSE_ID", "wh")
    monkeypatch.setattr(A, "_config", lambda: types.SimpleNamespace(host="https://h", authenticate=lambda: {}))
    fake_sql.msg = "[TABLE_OR_VIEW_NOT_FOUND] x"
    with pytest.raises(A.TableUnavailable):
        A.run_query("select 1")
    fake_sql.msg = "read timed out"
    with pytest.raises(RuntimeError):
        A.run_query("select 1")


def test_el_conector_sql_lleva_timeout():
    src = inspect.getsource(A.run_query)
    assert "_socket_timeout=SQL_TIMEOUT" in src and A.SQL_TIMEOUT > 0


def test_fila_sin_prediccion_no_se_rellena_con_cero(monkeypatch):
    bad = dict(GOOD, unit=2, rul_pred=None, estado="critical")
    nan = dict(GOOD, unit=3, rul_pred=float("nan"))
    monkeypatch.setattr(A, "run_query", lambda sql, p=None: [GOOD, bad, nan] if "gold_predicciones" in sql else
                        [{"model_version": 1, "rmse_test": 18.4, "phm08_test": 655.0, "trained_at": "t"}] if "gold_model_metrics" in sql
                        else [{"n": 5}] if "COUNT" in sql else [{"t": "2026-10-13"}])
    c = _client()
    e = {x["unit"]: x for x in c.get("/api/engines").json()["engines"]}
    for u in (2, 3):
        assert e[u]["rul_pred"] is None and e[u]["estado"] == "unknown"
        assert e[u]["action"] == "No prediction available for this engine."
    o = c.get("/api/overview").json()
    assert o["kpis"]["mean_rul"] == 10.0 and o["kpis"]["critical"] == 1   # nulos excluidos, no contados como RUL 0


def test_metricas_de_otra_version_no_se_muestran(monkeypatch):
    def q(sql, params=None):
        if "gold_predicciones" in sql:
            return [dict(GOOD, model_version=7)]
        if "gold_model_metrics" in sql:
            return []   # no hay metricas de la version 7
        if "COUNT" in sql:
            return [{"n": 1}]
        return [{"t": "x"}]
    monkeypatch.setattr(A, "run_query", q)
    j = _client().get("/api/overview").json()
    assert j["kpis"]["rmse"] is None and j["uncertainty"]["typical_error_cycles"] is None
    assert j["uncertainty"]["calibrated"] is False


def test_fallo_parcial_auxiliar_se_declara_en_errors(monkeypatch):
    def q(sql, params=None):
        if "gold_predicciones" in sql:
            return [GOOD]
        if "gold_model_metrics" in sql:
            return [{"model_version": 1, "rmse_test": 18.4, "phm08_test": 655.0, "trained_at": "t"}]
        if "COUNT" in sql:
            raise TimeoutError("slow")
        return [{"t": "x"}]
    monkeypatch.setattr(A, "run_query", q)
    j = _client().get("/api/overview").json()
    assert j["ready"] is True and "silver_lecturas" in j["errors"] and j["pipeline"]["layers"]["silver"] is None


@pytest.mark.parametrize("unit,code", [(0, 422), (-5, 422), (10001, 422)])
def test_motor_fuera_de_rango_422(monkeypatch, unit, code):
    monkeypatch.setattr(A, "run_query", lambda s, p=None: [])
    assert _client().get(f"/api/engines/{unit}/health").status_code == code


def test_motor_no_numerico_422_y_motor_inexistente_404(monkeypatch):
    monkeypatch.setattr(A, "run_query", lambda s, p=None: [])
    c = _client()
    assert c.get("/api/engines/abc/health").status_code == 422
    assert c.get("/api/engines/1.5/health").status_code == 422
    assert c.get("/api/engines/999/health").status_code == 404


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "  "}, {"question": "   \n "},
                                  {"question": "ab"}, {"question": "x" * 501}, {"question": None},
                                  {"question": 123}, {"q": "how often?"}])
def test_pregunta_mal_formada_422_sin_llamar_al_asistente(monkeypatch, body):
    llamado = []
    monkeypatch.setattr(A, "get_clients", lambda: llamado.append(1))
    r = _client().post("/api/ask", json=body)
    assert r.status_code == 422 and not llamado


def test_ask_con_json_invalido_422():
    r = _client().post("/api/ask", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_app_no_llama_al_endpoint_de_serving_ni_tiene_fallback_numerico():
    """Documenta el camino de codigo: la app solo lee tablas; no hay clientes de serving ni valores por defecto de RUL."""
    src = open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8").read()
    assert "/invocations" not in src and ".predict(" not in src and "serving_endpoints.query" not in src
    # the only serving-endpoint call is the metadata check of the chat endpoint in /api/health (no inference)
    import re
    assert re.findall(r"serving_endpoints\.\w+", src) == ["serving_endpoints.get"]
    assert "or 0.0" not in src
