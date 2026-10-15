"""B2: estimacion de DESARROLLO sin tocar el test. 5-fold por motor sobre train; para cada motor retenido se corta
la serie en un ciclo tal que su RUL real siga la distribucion de RUL de los 100 motores de test (solo la distribucion
marginal, no las etiquetas por motor). RUL real sin recorte. No es una evaluacion independiente: los hiperparametros
se eligieron con este mismo train."""
import json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import numpy as np, sklearn
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import GroupKFold
from lib.cmapss import read_cmapss, read_rul, rmse
from lib.modeling import HGB_PARAMS, build_frames
D = os.path.join(ROOT, "data", "cmapss")
rul_dist = read_rul(f"{D}/RUL_FD001.txt").rul_real.to_numpy()
fr = build_frames(read_cmapss(f"{D}/train_FD001.txt"), read_cmapss(f"{D}/test_FD001.txt"), read_rul(f"{D}/RUL_FD001.txt"))
X, y, g, tf, F = fr["X_train"], fr["y_train"], fr["groups"].to_numpy(), fr["train_frame"], fr["feature_columns"]
rng = np.random.default_rng(2026); folds = []
for tri, vai in GroupKFold(5).split(X, y, g):
    m = HistGradientBoostingRegressor(**HGB_PARAMS).fit(X.iloc[tri], y.iloc[tri])
    folds.append((m, vai))
oof = np.zeros(len(X))
for m, vai in folds: oof[vai] = m.predict(X.iloc[vai])
res = {"sklearn": sklearn.__version__, "oof_rmse_all_cycles_clipped_target": rmse(y, oof)}
reps = []
for rep in range(200):
    yt, pp = [], []
    for m, vai in folds:
        d = tf.iloc[vai]
        for u, du in d.groupby("unit"):
            mx = int(du.cycle.max())
            for _ in range(50):
                r = int(rng.choice(rul_dist)); cut = mx - r
                if cut >= 1: break
            else:
                cut = 1; r = mx - 1
            row = du[du.cycle == cut]
            pp.append(float(np.clip(m.predict(row[F].astype("float64")), 0, None)[0])); yt.append(float(r))
    reps.append(rmse(yt, pp))
res["truncated_heldout_rmse"] = {"repeats": 200, "mean": float(np.mean(reps)), "std": float(np.std(reps)),
                                 "p05": float(np.percentile(reps, 5)), "p95": float(np.percentile(reps, 95)), "n_engines_per_repeat": 100}
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev_protocol_heldout_train.json"), "w"), indent=1)
print(json.dumps(res, indent=1))
