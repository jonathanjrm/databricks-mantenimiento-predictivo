"""B2: reentrena HGB con los hiperparametros del proyecto sobre gold_features de Databricks (train) y evalua en las 100 filas finales de test del workspace."""
import json, sys, os, pickle
import numpy as np, pandas as pd, sklearn
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
from lib.cmapss import feature_columns, rmse, phm08_score
from lib.modeling import HGB_PARAMS
from sklearn.ensemble import HistGradientBoostingRegressor
S = sys.argv[1]; F = feature_columns()
tr = pd.read_pickle(S + "ws_train.pkl")
d = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace", "sql_gold_features_test_last_cycle_85feats.json")))
cols = [c["name"] for c in d["manifest"]["schema"]["columns"]]
te = pd.DataFrame(d["result"]["data_array"], columns=cols).astype(float).sort_values("unit")
out = {"sklearn": sklearn.__version__, "pandas": pd.__version__, "train_rows": len(tr)}
for label, frame in (("workspace_order", tr), ("shuffled_rows_seed0", tr.sample(frac=1, random_state=0))):
    m = HistGradientBoostingRegressor(**HGB_PARAMS).fit(frame[F], frame["rul"])
    p = np.clip(m.predict(te[F]), 0, None)
    out[label] = {"rmse": rmse(te.rul_real, p), "phm08": phm08_score(te.rul_real, p), "mae": float(np.mean(np.abs(p - te.rul_real)))}
# sensibilidad a ruido numerico 1e-9 relativo en las variables de train
rng = np.random.default_rng(0); r = []
for k in range(5):
    X = tr[F] * (1 + rng.normal(0, 1e-9, tr[F].shape))
    m = HistGradientBoostingRegressor(**HGB_PARAMS).fit(X, tr["rul"]); r.append(rmse(te.rul_real, np.clip(m.predict(te[F]), 0, None)))
out["rmse_with_1e-9_relative_noise_in_train_features"] = r
print(json.dumps(out, indent=1)); json.dump(out, open(f"ws_retrain_sklearn{sklearn.__version__.replace('.','')}.json", "w"), indent=1)
