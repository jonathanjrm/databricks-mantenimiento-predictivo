"""Comprobacion local (sin Databricks) del modelo de RUL con los datos reales FD001.

Usa la misma lógica que src/02_train.py (src/lib). Con --tune busca hiperparametros
con GroupKFold por motor solo sobre train. Escribe scripts/resultado_local.json.

Uso:  python scripts/entrenar_local.py [--tune]
"""
import argparse
import json
import os
import sys
import time
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np  # noqa: E402
import sklearn  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.model_selection import GroupKFold  # noqa: E402

from lib.cmapss import read_cmapss, read_rul, rmse  # noqa: E402
from lib.modeling import HGB_PARAMS, build_frames, evaluate, make_models  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    args = ap.parse_args()
    d = os.path.join(ROOT, "data", "cmapss")
    t0 = time.time()
    fr = build_frames(read_cmapss(f"{d}/train_FD001.txt"), read_cmapss(f"{d}/test_FD001.txt"),
                      read_rul(f"{d}/RUL_FD001.txt"))
    print(f"variables: {fr['X_train'].shape}, test: {fr['X_test'].shape} ({time.time()-t0:.1f}s)")
    params = dict(HGB_PARAMS)
    if args.tune:
        best = None
        grid = [dict(learning_rate=lr, max_leaf_nodes=nl, min_samples_leaf=msl, l2_regularization=l2)
                for lr in (0.05, 0.1) for nl in (15, 31) for msl in (40, 100) for l2 in (1.0,)]
        gkf = GroupKFold(n_splits=5)
        for g in grid:
            p = {**HGB_PARAMS, **g}
            errs = []
            for tri, vai in gkf.split(fr["X_train"], fr["y_train"], fr["groups"]):
                m = HistGradientBoostingRegressor(**p).fit(fr["X_train"].iloc[tri], fr["y_train"].iloc[tri])
                errs.append(rmse(fr["y_train"].iloc[vai], m.predict(fr["X_train"].iloc[vai])))
            score = float(np.mean(errs))
            print(g, round(score, 3))
            if best is None or score < best[0]:
                best = (score, p)
        params = best[1]
        print("mejor (cv en train):", params, round(best[0], 3))
    res = {}
    for name, model in make_models(params).items():
        model.fit(fr["X_train"], fr["y_train"])
        res[name] = evaluate(model, fr["X_test"], fr["y_test"])
        print(name, {k: round(v, 3) for k, v in res[name].items()})
    out = {"fecha": date.today().isoformat(), "dataset": "C-MAPSS FD001", "rul_clip": 125,
           "n_train_rows": int(len(fr["X_train"])), "n_test_units": int(len(fr["X_test"])),
           "n_features": len(fr["feature_columns"]), "sklearn": sklearn.__version__,
           "pandas": pd.__version__, "hgb_params": {k: v for k, v in params.items()},
           "resultados": res}
    with open(os.path.join(ROOT, "scripts", "resultado_local.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out["resultados"], indent=1))


if __name__ == "__main__":
    main()
