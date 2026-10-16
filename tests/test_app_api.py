"""API tests of the Databricks App backend with simulated SQL and assistant clients."""

import json
import math

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

import app as A
import rag
from health_projection import project_health

ROWS = [
    {
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
        "model_version": 3,
        "scored_at": "2026-10-13T00:00:00",
    },
    {
        "unit": 2,
        "cycle": 90,
        "rul_pred": 30.0,
        "rul_real": 28,
        "error": 2.0,
        "state": "watch",
        "sensor_1": "s4",
        "contrib_1": 5.0,
        "sensor_2": "s11",
        "contrib_2": 3.0,
        "sensor_3": "s2",
        "contrib_3": 1.0,
        "model_version": 3,
        "scored_at": "2026-10-13T00:00:00",
    },
    {
        "unit": 3,
        "cycle": 80,
        "rul_pred": 90.0,
        "rul_real": 95,
        "error": -5.0,
        "state": "healthy",
        "sensor_1": "s4",
        "contrib_1": 5.0,
        "sensor_2": "s11",
        "contrib_2": 3.0,
        "sensor_3": "s2",
        "contrib_3": 1.0,
        "model_version": 3,
        "scored_at": "2026-10-13T00:00:00",
    },
]


@pytest.fixture(autouse=True)
def clear_cache():
    A._cache.clear()
    yield
    A._cache.clear()


def _fake(sql, params=None):
    s = " ".join(sql.split())
    if "gold_predictions" in s:
        return ROWS
    if "gold_model_metrics" in s:
        return [{"model_version": 3, "rmse_test": 18.5, "phm08_test": 640.0, "trained_at": "2026-10-13"}]
    if "COUNT(*)" in s:
        return [{"n": 33727}]
    if "MAX(_ingest_ts)" in s:
        return [{"t": "2026-10-13T10:00:00+00:00"}]
    if "gold_health_curve" in s:
        return [{"cycle": c, "health_index": 0.01 * c} for c in range(1, 61)]
    return []


def test_security_headers_present_on_every_response():
    # D-06: CSP, frame and MIME-sniffing protection must be on every response, not just the
    # frontend page, and must still allow the app's own inline-script frontend to run.
    resp = TestClient(A.app).get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self' 'unsafe-inline'" in csp  # index.html's whole app is one inline <script>
    assert "fonts.googleapis.com" in csp and "fonts.gstatic.com" in csp


def test_overview_with_data(monkeypatch):
    monkeypatch.setattr(A, "run_query", _fake)
    j = TestClient(A.app).get("/api/overview").json()
    assert j["ready"] and j["kpis"]["engines"] == 3 and j["kpis"]["critical"] == 1
    assert j["kpis"]["mean_rul"] == pytest.approx(43.3, abs=0.1)
    assert j["kpis"]["rmse"] == 18.5 and j["model"]["version"] == 3


def test_empty_state_when_tables_are_missing(monkeypatch):
    def empty(sql, params=None):
        raise A.TableUnavailable("TABLE_OR_VIEW_NOT_FOUND")

    monkeypatch.setattr(A, "run_query", empty)
    c = TestClient(A.app)
    o = c.get("/api/overview")
    assert o.status_code == 200 and o.json()["ready"] is False and o.json()["missing"]
    e = c.get("/api/engines")
    assert e.status_code == 200 and e.json()["engines"] == []
    h = c.get("/api/engines/1/health")
    assert h.status_code == 200 and h.json()["ready"] is False


def test_health_curve_and_projection(monkeypatch):
    monkeypatch.setattr(A, "run_query", _fake)
    j = TestClient(A.app).get("/api/engines/1/health").json()
    assert j["ready"] and len(j["history"]) == 60
    # index = 0.01 * cycle: crosses 1.0 at cycle 100 -> 40 cycles ahead
    assert j["projection"]["cycles_to_threshold"] == 40 and j["projection"]["cross_cycle"] == 100
    assert TestClient(A.app).get("/api/engines/0/health").status_code == 422


def test_health_curve_drops_non_finite_readings(monkeypatch):
    # A corrupt gold_health_curve row (NaN/Inf health_index) must be dropped,
    # never serialized, so the endpoint stays valid JSON.
    def fake_with_bad_row(sql, params=None):
        s = " ".join(sql.split())
        if "gold_health_curve" in s:
            return [{"cycle": c, "health_index": 0.01 * c} for c in range(1, 61)] + [
                {"cycle": 61, "health_index": float("nan")},
                {"cycle": 62, "health_index": float("inf")},
            ]
        return _fake(sql, params)

    monkeypatch.setattr(A, "run_query", fake_with_bad_row)
    resp = TestClient(A.app).get("/api/engines/1/health")
    assert resp.status_code == 200
    j = resp.json()
    assert len(j["history"]) == 60  # the 2 corrupt rows were dropped
    assert all(math.isfinite(v) for _, v in j["history"])
    json.dumps(j, allow_nan=False)  # would raise ValueError if NaN/Inf leaked


def test_projection_without_trend():
    pts = [(c, 0.2 - 0.001 * c) for c in range(1, 40)]
    p = project_health(pts)
    assert p["cycles_to_threshold"] is None and p["projection"] == []
    assert project_health([(1, 0.1)])["slope"] is None


def test_projection_beyond_horizon_does_not_invent_a_crossing():
    # A true crossing around cycle 400 must not be reported as cycle 250.
    pts = [(c, 0.001 * c) for c in range(1, 31)]
    result = project_health(pts, threshold=1.0, max_ahead=250)
    assert result["slope"] == pytest.approx(0.001)
    assert result["cycles_to_threshold"] is None
    assert result["cross_cycle"] is None
    assert result["projection"] == []


def test_projection_at_horizon_remains_valid():
    pts = [(c, 0.01 * c) for c in range(1, 31)]
    result = project_health(pts, threshold=1.0, max_ahead=250)
    assert result["cycles_to_threshold"] == 70
    assert result["cross_cycle"] == 100


def test_projection_negative_slope_reports_no_crossing():
    # A clearly declining (improving) index must never report a future crossing.
    pts = [(c, 0.9 - 0.02 * c) for c in range(1, 20)]
    result = project_health(pts, threshold=1.0)
    assert result["cycles_to_threshold"] is None
    assert result["cross_cycle"] is None
    assert result["projection"] == []


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_projection_rejects_non_finite_readings(bad_value):
    # A NaN/Inf sensor reading must not leak into the fit or the JSON
    # response (NaN/Inf are not valid JSON): treat it as "no trend", not a crash.
    pts = [(c, 0.01 * c) for c in range(1, 9)] + [(9, bad_value)]
    result = project_health(pts, threshold=1.0)
    assert result["slope"] is None
    assert result["intercept"] is None
    assert result["cycles_to_threshold"] is None
    assert result["cross_cycle"] is None
    assert result["projection"] == []
    json.dumps(result, allow_nan=False)  # would raise ValueError if NaN/Inf leaked


def test_projection_single_duplicate_cycle_has_no_trend():
    # All points on the same cycle (den == 0): no slope can be fitted.
    result = project_health([(5, 0.1)] * 6, threshold=1.0)
    assert result["slope"] is None
    assert result["projection"] == []


def _chunks():
    return [
        {
            "chunk_id": "a",
            "source": "manual.pdf",
            "page": 7,
            "text": "Inspect every 500 hours. " * 40,
            "score": 0.9,
            "rrf": 0.03,
            "kind": "body",
        },
        {
            "chunk_id": "b",
            "source": "other.pdf",
            "page": 2,
            "text": "Unrelated text.",
            "score": 0.4,
            "kind": "glossary",
        },
    ]


def _answer(status="insufficient_evidence"):
    return rag.Answer(
        config="v2",
        question="q",
        queries=["borescope inspection"],
        retrieval_trace=[
            {
                "query": "borescope inspection",
                "results": [
                    {"chunk_id": "a", "source": "manual.pdf", "page": 7, "score": 0.9, "kind": "body"}
                ],
            }
        ],
        chunks=_chunks(),
        status=status,
        summary="Related coverage only.",
        claims=[
            {"text": "Inspect every 500 hours.", "citations": [1], "verdict": "SUPPORTED"},
            {"text": "Partly stated.", "citations": [1], "verdict": "PARTIAL"},
        ],
        removed_claims=[
            {
                "text": "Invented limit.",
                "citations": [2],
                "verdict": "NOT_SUPPORTED",
                "reason": "not in passage",
            }
        ],
        gaps="Engine-specific intervals.",
        manufacturer_note="Refer to the manufacturer manual.",
    )


def test_ask_returns_structured_answer(monkeypatch, caplog):
    seen = {}

    def fake_answer(question, llm, search, config, kinds=None):
        seen.update(question=question, config=config, kinds=kinds)
        return _answer()

    monkeypatch.setattr(A, "get_clients", lambda: (lambda m, t=800: "", lambda q, n: []))
    monkeypatch.setattr(A, "get_kinds", lambda: {("manual.pdf", 7): "body"})
    monkeypatch.setattr(A.rag, "answer", fake_answer)
    with caplog.at_level("INFO", logger="maintenance"):
        j = TestClient(A.app).post("/api/ask", json={"question": "how often to inspect?"}).json()
    assert seen["config"] == "v2" and seen["kinds"] == {("manual.pdf", 7): "body"}
    assert j["status"] == "insufficient_evidence" and j["gaps"] and j["manufacturer_note"]
    assert [c["verdict"] for c in j["claims"]] == ["SUPPORTED", "PARTIAL"]
    assert j["removed_claims"][0]["reason"] == "not in passage" and "reason" not in j["claims"][0]
    s0, s1 = j["sources"]
    assert s0["cited"] is True and s1["cited"] is False  # [2] only cited by a REMOVED claim
    assert len(s0["snippet"]) == 300 and s0["kind"] == "body" and s0["rrf"] == 0.03
    assert j["trace"]["config"] == "v2" and j["trace"]["queries"] == ["borescope inspection"]
    assert j["trace"]["retrieval"][0]["results"][0]["page"] == 7
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("ask "))
    assert "status=insufficient_evidence" in line and "claims=2" in line and "removed=1" in line
    assert "manual.pdf:p7" in line and "Inspect every" not in line


def test_ask_with_real_rag_and_fake_clients(monkeypatch):
    def llm(messages, max_tokens=800):
        sysm = messages[0]["content"]
        if "search query" in sysm.lower() or "queries" in sysm.lower():
            return '["borescope inspection"]'
        if "check whether a claim" in sysm:
            return '{"verdict": "SUPPORTED", "reason": "stated"}'
        return (
            '{"status": "answered", "summary": "Yes.", "claims": [{"text": "Inspect every 500 hours.", '
            '"citations": [1]}], "gaps": "", "manufacturer_note": ""}'
        )

    monkeypatch.setattr(A, "get_clients", lambda: (llm, lambda q, n: _chunks()))
    monkeypatch.setattr(A, "get_kinds", lambda: None)
    j = TestClient(A.app).post("/api/ask", json={"question": "how often to inspect?"}).json()
    # "how often" asks for an interval: values are withheld and the manufacturer is required.
    assert j["status"] == "requires_manufacturer_documentation" and j["sources"][0]["source"] == "manual.pdf"
    assert j["claims"] == [] and j["removed_claims"][0]["verdict"] == "WITHHELD"
    assert "trace" in j and j["trace"]["config"] == "v2"
    assert any("requires_manufacturer_documentation" in x for x in j["trace"]["adjustments"])


def test_ask_reports_clear_errors(monkeypatch):
    def boom(q, n):
        raise RuntimeError("index not found")

    monkeypatch.setattr(A, "get_clients", lambda: (lambda m, t=800: "[]", boom))
    monkeypatch.setattr(A, "get_kinds", lambda: None)
    r = TestClient(A.app).post("/api/ask", json={"question": "hello world"})
    assert r.status_code == 503 and r.json()["error"] == "index_unavailable"
    assert "not available" in r.json()["message"]

    def llm_boom(m, t=800):
        raise RuntimeError("endpoint down")

    monkeypatch.setattr(A, "get_clients", lambda: (llm_boom, lambda q, n: _chunks()))
    r = TestClient(A.app).post("/api/ask", json={"question": "hello world"})
    assert (
        r.status_code == 502
        and r.json()["error"] == "llm_unavailable"
        and "Could not query" in r.json()["message"]
    )
    assert TestClient(A.app).post("/api/ask", json={"question": "a"}).status_code == 422


def test_get_kinds_is_cached_and_tolerant(monkeypatch):
    calls = []

    def q(sql, params=None):
        calls.append(sql)
        return [{"source": "m.pdf", "page": 3, "text": "G-3 Glossary entry"}]

    monkeypatch.setattr(A, "run_query", q)
    monkeypatch.setattr(A, "_kinds", (0.0, None))
    assert A.get_kinds() == {("m.pdf", 3): "glossary"} and A.get_kinds() is not None
    assert len(calls) == 1 and "chunk_idx = 0" in calls[0]
    monkeypatch.setattr(A, "_kinds", (0.0, None))
    monkeypatch.setattr(A, "run_query", lambda s, p=None: (_ for _ in ()).throw(RuntimeError("x")))
    assert A.get_kinds() is None


def test_states_and_english_actions(monkeypatch):
    monkeypatch.setattr(A, "run_query", _fake)
    j = TestClient(A.app).get("/api/engines").json()
    assert [e["state"] for e in j["engines"]] == ["critical", "watch", "healthy"]
    assert j["engines"][0]["action"].startswith(
        "Demo estimate; requires engineering review. Estimated RUL is low (about 10 cycles)"
    )
    assert "Sensors to check first: s4 and s11." in j["engines"][0]["action"]
    assert "No degradation flagged" in j["engines"][2]["action"]
    assert j["sensor_names"]["s4"] == "T50 LPT outlet temperature"


def test_front_page():
    r = TestClient(A.app).get("/")
    assert r.status_code == 200 and "Predictive turbine maintenance" in r.text


def test_action_text_identical_in_app_and_lib():
    from lib import cmapss as C

    for st in ("critical", "watch", "healthy", "unknown"):
        for rul in (0.0, 12.4, 40.0, 100.0):
            assert A.recommended_action(st, rul, ["s4", "s11", "s2"]) == C.recommended_action(
                st, rul, ["s4", "s11", "s2"]
            )
