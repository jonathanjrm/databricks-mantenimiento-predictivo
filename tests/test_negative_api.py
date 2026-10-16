"""Negative API tests (audit B4): on failure the app reports and does NOT invent predictions.

The app does not call the serving endpoint: it reads the batch scoring table (gold_predictions).
These tests simulate SQL failures, timeouts, a missing table, an unconfigured warehouse and incomplete rows.
"""

import inspect
import os
import re
import sys
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

import app as A

GOOD = {
    "unit": 1,
    "cycle": 100,
    "rul_pred": 10.0,
    "rul_real": 12,
    "error": -2.0,
    "state": "critical",
    "sensor_1": "s4",
    "contrib_1": 5.0,
    "sensor_2": "s11",
    "contrib_2": 3.0,
    "sensor_3": "s2",
    "contrib_3": 1.0,
    "model_version": 1,
    "scored_at": "2026-10-13T00:00:00",
}


@pytest.fixture(autouse=True)
def clear_cache():
    A._cache.clear()
    yield
    A._cache.clear()


def _client():
    return TestClient(A.app, raise_server_exceptions=False)


def _boom(exc):
    def f(sql, params=None):
        raise exc

    return f


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("SQL syntax error"),
        TimeoutError("socket timeout"),
        ConnectionError("warehouse unreachable"),
    ],
)
def test_sql_failure_is_an_explicit_503_without_invented_data(monkeypatch, exc):
    monkeypatch.setattr(A, "run_query", _boom(exc))
    c = _client()
    for url in ("/api/engines", "/api/overview", "/api/engines/1/health"):
        r = c.get(url)
        assert r.status_code == 503, url
        j = r.json()
        assert j["error"] == "data_unavailable" and "No prediction" in j["message"]
        assert "engines" not in j and "kpis" not in j and "history" not in j


def test_missing_table_is_an_explicit_empty_state(monkeypatch):
    monkeypatch.setattr(A, "run_query", _boom(A.TableUnavailable("TABLE_OR_VIEW_NOT_FOUND")))
    j = _client().get("/api/engines").json()
    assert j["ready"] is False and j["engines"] == [] and j["missing"] == ["gold_predictions"]


def test_unconfigured_warehouse_is_empty_state_not_a_prediction(monkeypatch):
    monkeypatch.setattr(A, "WAREHOUSE_ID", "")
    j = _client().get("/api/engines").json()
    assert j["ready"] is False and j["engines"] == []


def test_run_query_translates_missing_table_and_propagates_other_errors(monkeypatch):
    class FakeConn:
        def __init__(self, msg):
            self.msg = msg

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return self

        def execute(self, *a, **k):
            raise RuntimeError(self.msg)

    fake_sql = types.SimpleNamespace(connect=lambda **k: FakeConn(fake_sql.msg))
    pkg = types.ModuleType("databricks")
    pkg.sql = fake_sql
    monkeypatch.setitem(sys.modules, "databricks", pkg)
    monkeypatch.setitem(sys.modules, "databricks.sql", fake_sql)
    monkeypatch.setattr(A, "WAREHOUSE_ID", "wh")
    monkeypatch.setattr(
        A, "_config", lambda: types.SimpleNamespace(host="https://h", authenticate=lambda: {})
    )
    fake_sql.msg = "[TABLE_OR_VIEW_NOT_FOUND] x"
    with pytest.raises(A.TableUnavailable):
        A.run_query("select 1")
    fake_sql.msg = "read timed out"
    with pytest.raises(RuntimeError):
        A.run_query("select 1")


def test_sql_connector_has_a_timeout():
    src = inspect.getsource(A.run_query)
    assert "_socket_timeout=SQL_TIMEOUT" in src and A.SQL_TIMEOUT > 0


def test_row_without_prediction_is_not_filled_with_zero(monkeypatch):
    bad = dict(GOOD, unit=2, rul_pred=None, state="critical")
    nan = dict(GOOD, unit=3, rul_pred=float("nan"))

    def q(sql, p=None):
        if "gold_predictions" in sql:
            return [GOOD, bad, nan]
        if "gold_model_metrics" in sql:
            return [{"model_version": 1, "rmse_test": 18.4, "phm08_test": 655.0, "trained_at": "t"}]
        if "COUNT" in sql:
            return [{"n": 5}]
        return [{"t": "2026-10-13"}]

    monkeypatch.setattr(A, "run_query", q)
    c = _client()
    e = {x["unit"]: x for x in c.get("/api/engines").json()["engines"]}
    for u in (2, 3):
        assert e[u]["rul_pred"] is None and e[u]["state"] == "unknown"
        assert e[u]["action"] == "No prediction available for this engine."
    o = c.get("/api/overview").json()
    assert (
        o["kpis"]["mean_rul"] == 10.0 and o["kpis"]["critical"] == 1
    )  # nulls excluded, not counted as RUL 0


def test_metrics_of_another_version_are_not_shown(monkeypatch):
    def q(sql, params=None):
        if "gold_predictions" in sql:
            return [dict(GOOD, model_version=7)]
        if "gold_model_metrics" in sql:
            return []  # no metrics for version 7
        if "COUNT" in sql:
            return [{"n": 1}]
        return [{"t": "x"}]

    monkeypatch.setattr(A, "run_query", q)
    j = _client().get("/api/overview").json()
    assert j["kpis"]["rmse"] is None and j["uncertainty"]["typical_error_cycles"] is None
    assert j["uncertainty"]["calibrated"] is False


def test_partial_auxiliary_failure_is_declared_in_errors(monkeypatch):
    def q(sql, params=None):
        if "gold_predictions" in sql:
            return [GOOD]
        if "gold_model_metrics" in sql:
            return [{"model_version": 1, "rmse_test": 18.4, "phm08_test": 655.0, "trained_at": "t"}]
        if "COUNT" in sql:
            raise TimeoutError("slow")
        return [{"t": "x"}]

    monkeypatch.setattr(A, "run_query", q)
    j = _client().get("/api/overview").json()
    assert (
        j["ready"] is True and "silver_readings" in j["errors"] and j["pipeline"]["layers"]["silver"] is None
    )


@pytest.mark.parametrize("unit,code", [(0, 422), (-5, 422), (10001, 422)])
def test_engine_out_of_range_is_422(monkeypatch, unit, code):
    monkeypatch.setattr(A, "run_query", lambda s, p=None: [])
    assert _client().get(f"/api/engines/{unit}/health").status_code == code


def test_non_numeric_engine_is_422_and_unknown_engine_is_404(monkeypatch):
    monkeypatch.setattr(A, "run_query", lambda s, p=None: [])
    c = _client()
    assert c.get("/api/engines/abc/health").status_code == 422
    assert c.get("/api/engines/1.5/health").status_code == 422
    assert c.get("/api/engines/999/health").status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "  "},
        {"question": "   \n "},
        {"question": "ab"},
        {"question": "x" * 501},
        {"question": None},
        {"question": 123},
        {"q": "how often?"},
    ],
)
def test_malformed_question_is_422_without_calling_the_assistant(monkeypatch, body):
    called = []
    monkeypatch.setattr(A, "get_clients", lambda: called.append(1))
    r = _client().post("/api/ask", json=body)
    assert r.status_code == 422 and not called


def test_ask_with_invalid_json_is_422():
    r = _client().post("/api/ask", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_app_does_not_call_the_serving_endpoint_and_has_no_numeric_fallback():
    """Documents the code path: the app only reads tables; no serving clients and no default RUL values."""
    with open(os.path.join(os.path.dirname(A.__file__), "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "/invocations" not in src and ".predict(" not in src and "serving_endpoints.query" not in src
    # the only serving-endpoint call is the metadata check of the chat endpoint in /api/health (no inference)
    assert re.findall(r"serving_endpoints\.\w+", src) == ["serving_endpoints.get"]
    assert "or 0.0" not in src
