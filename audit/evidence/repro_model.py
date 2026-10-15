"""Reproduccion controlada del modelo de RUL (auditoria B2). Uso: python repro_model.py <salida.json> [--cv]

Usa exactamente src/lib (pandas). No toca el test para decidir nada: solo informa.
"""
import json, os, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import numpy as np, pandas as pd, sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge, LinearRegression
from sklearn.model_selection import GroupKFold
from lib.cmapss import read_cmapss, read_rul, rmse, phm08_score
from lib.modeling import HGB_PARAMS, build_frames, make_models, evaluate

out_path = sys.argv[1]
D = os.path.join(ROOT, "data", "cmapss")
fr = build_frames(read_cmapss(f"{D}/train_FD001.txt"), read_cmapss(f"{D}/test_FD001.txt"), read_rul(f"{D}/RUL_FD001.txt"))
Xtr, ytr, Xte, yte = fr["X_train"], fr["y_train"], fr["X_test"], fr["y_test"]
units = fr["test_units"].to_numpy()
res = {"versions": {"python": sys.version.split()[0], "sklearn": sklearn.__version__, "pandas": pd.__version__, "numpy": np.__version__},
       "hgb_params": HGB_PARAMS, "n_train_rows": len(Xtr), "n_features": Xtr.shape[1], "n_test_units": len(Xte)}

models = make_models()
preds = {}
for name, m in models.items():
    m.fit(Xtr, ytr)
    p = np.clip(m.predict(Xte), 0, None)
    preds[name] = p
# baseline extra: solo la edad (ciclo) con regresion lineal
lin = LinearRegression().fit(Xtr[["cycle"]], ytr)
preds["baseline_solo_ciclo_lineal"] = np.clip(lin.predict(Xte[["cycle"]]), 0, None)

def metrics(y, p):
    y = np.asarray(y, float); p = np.asarray(p, float)
    ss = ((y - p) ** 2).sum(); st = ((y - y.mean()) ** 2).sum()
    return {"rmse": rmse(y, p), "mae": float(np.mean(np.abs(p - y))), "r2": float(1 - ss / st),
            "phm08": phm08_score(y, p), "bias_mean_pred_minus_real": float(np.mean(p - y)),
            "late_pct": float(np.mean(p > y) * 100)}
res["metrics_test_last_cycle"] = {k: metrics(yte, v) for k, v in preds.items()}

# por intervalos de RUL real
bins = [(0, 30), (31, 60), (61, 90), (91, 125), (126, 1000)]
rows = []
for k, p in preds.items():
    for lo, hi in bins:
        mk = (yte.to_numpy() >= lo) & (yte.to_numpy() <= hi)
        if mk.sum():
            rows.append({"model": k, "rul_real_bin": f"{lo}-{hi}", "n": int(mk.sum()),
                         "rmse": rmse(yte[mk], p[mk]), "mae": float(np.mean(np.abs(p[mk] - yte[mk]))),
                         "bias": float(np.mean(p[mk] - yte[mk]))})
res["by_rul_bin"] = rows
mk = yte.to_numpy() <= 30
res["near_failure_rul_le_30"] = {k: {"n": int(mk.sum()), **metrics(yte[mk], p[mk])} for k, p in preds.items() if k != "baseline_media"}

per = pd.DataFrame({"unit": units, "cycle_final": fr["test_frame"].groupby("unit")["cycle"].max().reindex(units).to_numpy(),
                    "rul_real": yte.to_numpy(), **{f"pred_{k}": v for k, v in preds.items()}})
per["error_hgb"] = per["pred_hist_gradient_boosting"] - per["rul_real"]
per["abs_error_hgb"] = per["error_hgb"].abs()
per.round(4).to_csv(out_path.replace(".json", "_per_engine.csv"), index=False)
res["worst_10_engines_hgb"] = per.nlargest(10, "abs_error_hgb")[["unit", "rul_real", "pred_hist_gradient_boosting", "error_hgb"]].round(2).to_dict("records")

# determinismo / semilla
seeds = {}
for s in (0, 1, 42, 123):
    m = HistGradientBoostingRegressor(**{**HGB_PARAMS, "random_state": s}).fit(Xtr, ytr)
    seeds[str(s)] = rmse(yte, np.clip(m.predict(Xte), 0, None))
res["rmse_by_seed_hgb"] = seeds

if "--cv" in sys.argv:
    # Protocolo de desarrollo honesto: solo train, GroupKFold por motor
    gkf = GroupKFold(n_splits=5)
    grid = [dict(learning_rate=lr, max_leaf_nodes=nl, min_samples_leaf=msl, l2_regularization=1.0)
            for lr in (0.05, 0.1) for nl in (15, 31) for msl in (40, 100)]
    g = fr["groups"].to_numpy()
    cv = []
    for gp in grid:
        p = {**HGB_PARAMS, **gp}
        e = []
        for tri, vai in gkf.split(Xtr, ytr, g):
            m = HistGradientBoostingRegressor(**p).fit(Xtr.iloc[tri], ytr.iloc[tri])
            e.append(rmse(ytr.iloc[vai], m.predict(Xtr.iloc[vai])))
        cv.append({**gp, "cv_rmse_clipped_target": float(np.mean(e))})
    res["cv_grid_train_only"] = cv
    res["cv_best"] = min(cv, key=lambda r: r["cv_rmse_clipped_target"])
    # Evaluacion de desarrollo en motores de train retenidos, truncados en ciclo aleatorio (como el test), RUL real sin recorte
    rng = np.random.default_rng(2026)
    tf = fr["train_frame"]; feats = fr["feature_columns"]
    errs_all = []
    for rep in range(20):
        yt, pp = [], []
        for tri, vai in GroupKFold(n_splits=5).split(Xtr, ytr, g):
            m = HistGradientBoostingRegressor(**HGB_PARAMS).fit(Xtr.iloc[tri], ytr.iloc[tri])
            vfr = tf.iloc[vai]
            for u, d in vfr.groupby("unit"):
                mx = d["cycle"].max()
                cut = int(rng.integers(max(31, 1), mx))  # ciclo de corte aleatorio
                row = d[d["cycle"] == cut]
                pr = float(np.clip(m.predict(row[feats].astype("float64")), 0, None)[0])
                yt.append(float(mx - cut)); pp.append(pr)
        errs_all.append(rmse(yt, pp))
    res["dev_truncated_heldout_train_engines"] = {"repeats": 20, "rmse_mean": float(np.mean(errs_all)), "rmse_std": float(np.std(errs_all)),
        "note": "5-fold por motor sobre train, un corte aleatorio por motor y repeticion, RUL real sin recorte. Estimacion de desarrollo, no evaluacion independiente (los hiperparametros se eligieron en este mismo train)."}
json.dump(res, open(out_path, "w"), indent=2, default=float)
print(json.dumps(res["metrics_test_last_cycle"], indent=1)[:1500])
