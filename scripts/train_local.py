"""Local check (no Databricks) of the RUL model on the real FD001 data.

Uses the same logic as src/02_train.py (src/lib). With --tune it searches hyperparameters
with GroupKFold by engine on train only. Writes scripts/results_local.json.

Usage:  python scripts/train_local.py [--tune]
"""

import argparse
import json
import os
import sys
import time
from datetime import date

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold

from lib.cmapss import RUL_CLIP, read_cmapss, read_rul, rmse
from lib.modeling import HGB_PARAMS, build_frames, evaluate, make_models

TUNE_GRID = [
    {"learning_rate": lr, "max_leaf_nodes": nl, "min_samples_leaf": msl, "l2_regularization": l2}
    for lr in (0.05, 0.1)
    for nl in (15, 31)
    for msl in (40, 100)
    for l2 in (1.0,)
]
N_SPLITS = 5


def tune(fr: dict) -> dict:
    """Grid search with GroupKFold by engine on train only; returns the best parameter set."""
    best = None
    gkf = GroupKFold(n_splits=N_SPLITS)
    for g in TUNE_GRID:
        p = {**HGB_PARAMS, **g}
        errs = []
        for tri, vai in gkf.split(fr["X_train"], fr["y_train"], fr["groups"]):
            m = HistGradientBoostingRegressor(**p).fit(fr["X_train"].iloc[tri], fr["y_train"].iloc[tri])
            errs.append(rmse(fr["y_train"].iloc[vai], m.predict(fr["X_train"].iloc[vai])))
        score = float(np.mean(errs))
        print(g, round(score, 3))
        if best is None or score < best[0]:
            best = (score, p)
    print("best (cv on train):", best[1], round(best[0], 3))
    return best[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", action="store_true")
    args = ap.parse_args()
    d = os.path.join(ROOT, "data", "cmapss")
    t0 = time.time()
    fr = build_frames(
        read_cmapss(f"{d}/train_FD001.txt"),
        read_cmapss(f"{d}/test_FD001.txt"),
        read_rul(f"{d}/RUL_FD001.txt"),
    )
    print(f"features: {fr['X_train'].shape}, test: {fr['X_test'].shape} ({time.time() - t0:.1f}s)")
    params = tune(fr) if args.tune else dict(HGB_PARAMS)
    res = {}
    for name, model in make_models(params).items():
        model.fit(fr["X_train"], fr["y_train"])
        res[name] = evaluate(model, fr["X_test"], fr["y_test"])
        print(name, {k: round(v, 3) for k, v in res[name].items()})
    out = {
        "date": date.today().isoformat(),
        "dataset": "C-MAPSS FD001",
        "rul_clip": RUL_CLIP,
        "n_train_rows": len(fr["X_train"]),
        "n_test_units": len(fr["X_test"]),
        "n_features": len(fr["feature_columns"]),
        "sklearn": sklearn.__version__,
        "pandas": pd.__version__,
        "hgb_params": dict(params),
        "results": res,
    }
    with open(os.path.join(ROOT, "scripts", "results_local.json"), "w") as fh:
        json.dump(out, fh, indent=2)
    print(json.dumps(out["results"], indent=1))


if __name__ == "__main__":
    main()
