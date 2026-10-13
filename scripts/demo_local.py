"""Demo local de la app SIN Databricks: genera las tablas gold con pandas y sirve la interfaz.

Sirve para revisar el diseño y el flujo en tu maquina. Usa los mismos datos y la misma
lógica de src/lib que el pipeline, pero las tablas son datos en memoria, el asistente de
manuales responde con un texto fijo y no se contacta con ningún workspace.

Uso:  pip install fastapi uvicorn pandas scikit-learn
      python scripts/demo_local.py [--port 8000]
"""
import argparse
import datetime as dt
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "app"))

import numpy as np  # noqa: E402

from lib.cmapss import (ACTIVE_SENSORS, HEALTHY_CYCLES, classify_state, fit_health_params,  # noqa: E402
                        health_index, occlusion_contributions, read_cmapss, read_rul, top_k_sensors)
from lib.modeling import build_frames, evaluate, make_models  # noqa: E402


def build_tables():
    d = os.path.join(ROOT, "data", "cmapss")
    train, test, rul = read_cmapss(f"{d}/train_FD001.txt"), read_cmapss(f"{d}/test_FD001.txt"), read_rul(f"{d}/RUL_FD001.txt")
    fr = build_frames(train, test, rul)
    model = make_models()["hist_gradient_boosting"].fit(fr["X_train"], fr["y_train"])
    metrics = evaluate(model, fr["X_test"], fr["y_test"])
    pred = np.clip(model.predict(fr["X_test"]), 0, None)
    healthy = fr["train_frame"][fr["train_frame"]["cycle"] <= HEALTHY_CYCLES][fr["feature_columns"]].median()
    contrib = occlusion_contributions(model.predict, fr["X_test"], healthy, ACTIVE_SENSORS)
    top = top_k_sensors(contrib, 3)
    last_cycles = fr["test_frame"].groupby("unit")["cycle"].max()
    rows = []
    for i, u in enumerate(fr["test_units"]):
        r = {"unit": int(u), "cycle": int(last_cycles[u]), "rul_pred": round(float(pred[i]), 2),
             "rul_real": int(fr["y_test"].iloc[i]), "error": round(float(pred[i] - fr["y_test"].iloc[i]), 2),
             "estado": classify_state(pred[i]), "model_version": 1, "scored_at": dt.datetime.now().isoformat(),
             "umbral_critico": 20.0, "umbral_vigilar": 35.0}
        for k in range(3):
            r[f"sensor_{k+1}"], r[f"contrib_{k+1}"] = top[i][k][0], round(top[i][k][1], 3)
        rows.append(r)
    params = fit_health_params(train)
    tt = test.copy()
    tt["hi"] = health_index(tt, params)
    curves = {int(u): [{"cycle": int(c), "health_index": float(h)} for c, h in zip(g["cycle"], g["hi"])]
              for u, g in tt.groupby("unit")}
    return rows, metrics, curves, (len(train) + len(test), len(train) + len(test))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    rows, m, curves, (nb, ns) = build_tables()
    import app as A

    def fake_query(sql, params=None):
        s = " ".join(sql.split())
        if "gold_predicciones" in s:
            return rows
        if "gold_model_metrics" in s:
            return [{"model_version": 1, "rmse_test": m["rmse_test"], "phm08_test": m["phm08_test"],
                     "trained_at": dt.datetime.now().isoformat()}]
        if "COUNT(*)" in s:
            return [{"n": {"bronze_cmapss_lecturas": nb, "silver_lecturas": ns}.get(
                next((t for t in ("bronze_cmapss_lecturas", "silver_lecturas") if t in s), ""), 33727)}]
        if "DESCRIBE DETAIL" in s:
            return [{"lastModified": dt.datetime.now(dt.timezone.utc).isoformat()}]
        if "gold_salud_curva" in s:
            return curves.get(int(params["u"]), [])
        return []

    A.run_query = fake_query
    A.retrieve = lambda q, k=4: [{"chunk_id": "x", "source": "manual_demo.pdf", "page": 12,
                                  "text": "Texto de ejemplo del manual sobre " + q, "score": 0.5}]
    A.call_llm = lambda messages: "Respuesta de demostracion basada en el manual [1]. (Modo local: no hay LLM conectado.)"
    import uvicorn
    uvicorn.run(A.app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
