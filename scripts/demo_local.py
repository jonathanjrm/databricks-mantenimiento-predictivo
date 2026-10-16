"""Local demo of the app WITHOUT Databricks: builds the gold tables with pandas and serves the UI.

Useful to review the design and the flow on your machine. It uses the same data and the same
src/lib logic as the pipeline, but the tables are in-memory data, the manuals assistant answers
from a fake LLM and index, and no workspace is contacted.

Usage:  pip install fastapi uvicorn pandas scikit-learn
        python scripts/demo_local.py [--port 8000]
"""

import argparse
import datetime as dt
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "app"))

import numpy as np

from lib.cmapss import (
    ACTIVE_SENSORS,
    HEALTHY_CYCLES,
    classify_state,
    fit_health_params,
    health_index,
    occlusion_contributions,
    read_cmapss,
    read_rul,
    top_k_sensors,
)
from lib.modeling import CANDIDATE_MODEL, build_frames, evaluate, make_models

CRITICAL_THRESHOLD = 20.0
WATCH_THRESHOLD = 35.0
TOP_SENSORS = 3
DEMO_CHUNK = {
    "chunk_id": "demo",
    "source": "manual_demo.pdf",
    "page": 12,
    "text": "Inspect the compressor section for nicks, dents and cracks. Any inspection and repair "
    "requires the manufacturer's current information for limits.",
    "score": 0.5,
}


def build_tables() -> tuple[list[dict], dict, dict[int, list[dict]], int]:
    d = os.path.join(ROOT, "data", "cmapss")
    train = read_cmapss(f"{d}/train_FD001.txt")
    test = read_cmapss(f"{d}/test_FD001.txt")
    rul = read_rul(f"{d}/RUL_FD001.txt")
    fr = build_frames(train, test, rul)
    model = make_models()[CANDIDATE_MODEL].fit(fr["X_train"], fr["y_train"])
    metrics = evaluate(model, fr["X_test"], fr["y_test"])
    pred = np.clip(model.predict(fr["X_test"]), 0, None)
    healthy = fr["train_frame"][fr["train_frame"]["cycle"] <= HEALTHY_CYCLES][fr["feature_columns"]].median()
    contrib = occlusion_contributions(model.predict, fr["X_test"], healthy, ACTIVE_SENSORS)
    top = top_k_sensors(contrib, TOP_SENSORS)
    last_cycles = fr["test_frame"].groupby("unit")["cycle"].max()
    rows = []
    for i, u in enumerate(fr["test_units"]):
        r = {
            "unit": int(u),
            "cycle": int(last_cycles[u]),
            "rul_pred": round(float(pred[i]), 2),
            "rul_real": int(fr["y_test"].iloc[i]),
            "error": round(float(pred[i] - fr["y_test"].iloc[i]), 2),
            "state": classify_state(pred[i], CRITICAL_THRESHOLD, WATCH_THRESHOLD),
            "model_version": 1,
            "scored_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "critical_threshold": CRITICAL_THRESHOLD,
            "watch_threshold": WATCH_THRESHOLD,
        }
        for k in range(TOP_SENSORS):
            r[f"sensor_{k + 1}"], r[f"contrib_{k + 1}"] = top[i][k][0], round(top[i][k][1], 3)
        rows.append(r)
    params = fit_health_params(train)
    tt = test.copy()
    tt["hi"] = health_index(tt, params)
    curves = {
        int(u): [{"cycle": int(c), "health_index": float(h)} for c, h in zip(g["cycle"], g["hi"])]
        for u, g in tt.groupby("unit")
    }
    return rows, metrics, curves, len(train) + len(test)


def fake_llm(messages: list, max_tokens: int = 800) -> str:
    """Answers the three prompts of app/rag.py (query rewrite, structured answer, judge) with fixed text."""
    system = messages[0]["content"].lower()
    if "search queries" in system:
        return '["compressor section inspection"]'
    if "check whether a claim" in system:
        return '{"verdict": "SUPPORTED", "reason": "stated in the passage (local demo)"}'
    if "how well the numbered passages cover" in system:
        return '{"coverage": "general_only", "explanation": "Local demo: fixed answer, no real index."}'
    return (
        '{"status": "insufficient_evidence", "summary": "Local demo answer; no LLM is connected.", '
        '"claims": [{"text": "Inspect the compressor section for nicks, dents and cracks.", '
        '"citations": [1]}], '
        '"gaps": "This is a local demo without the manuals index.", '
        '"manufacturer_note": "Any inspection and repair requires the manufacturer\'s current information."}'
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    rows, m, curves, n_readings = build_tables()
    import app as A

    def fake_query(sql: str, params: dict | None = None) -> list[dict]:
        s = " ".join(sql.split())
        if "gold_predictions" in s:
            return rows
        if "gold_model_metrics" in s:
            return [
                {
                    "model_version": 1,
                    "rmse_test": m["rmse_test"],
                    "phm08_test": m["phm08_test"],
                    "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                }
            ]
        if "COUNT(*)" in s:
            return [{"n": n_readings}]
        if "MAX(_ingest_ts)" in s:
            return [{"t": dt.datetime.now(dt.timezone.utc).isoformat()}]
        if "gold_health_curve" in s:
            return curves.get(int(params["u"]), [])
        if "SELECT 1" in s:
            return [{"ok": 1}]
        return []

    A.run_query = fake_query
    A.get_clients = lambda: (fake_llm, lambda q, n: [dict(DEMO_CHUNK)])
    A.get_kinds = lambda: None
    A.CHECKS = {
        "sql_warehouse": lambda: {"status": "ok", "detail": "local demo"},
        "search_index": lambda: {"status": "ok", "detail": "local demo"},
        "llm": lambda: {"status": "ok", "detail": "local demo"},
    }
    import uvicorn

    uvicorn.run(A.app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
