"""B2.4: metrica de seleccion del nuevo protocolo (validacion por motor, SOLO train) para el candidato HGB
y el baseline Ridge, con los mismos hiperparametros del champion v1. Resultado -> validation_protocol_train_only.json.
No usa el conjunto de test de FD001 en ningun paso."""
import json, os, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import sklearn, pandas, numpy
from lib.cmapss import RUL_CLIP, add_rul_train, feature_columns, read_cmapss, rolling_features
from lib.modeling import HGB_PARAMS, make_models, validation_rmse_by_engine, promotion_decision
D = os.path.join(ROOT, "data", "cmapss")
tf = rolling_features(add_rul_train(read_cmapss(f"{D}/train_FD001.txt"), RUL_CLIP))
feats = feature_columns()
out = {"sklearn": sklearn.__version__, "pandas": pandas.__version__, "numpy": numpy.__version__,
       "hgb_params": {k: str(v) for k, v in HGB_PARAMS.items()}, "n_train_rows": int(len(tf)), "n_features": len(feats)}
for name in ("baseline_ridge", "hist_gradient_boosting"):
    t0 = time.time()
    out[name] = validation_rmse_by_engine(lambda: make_models()[name], tf, feats)
    out[name]["seconds"] = round(time.time() - t0, 1)
    print(name, out[name])
out["decision_example_vs_ridge_as_champion"] = promotion_decision(
    0, out["baseline_ridge"]["rmse_val_truncated"], out["hist_gradient_boosting"]["rmse_val_truncated"],
    out["hist_gradient_boosting"]["std_folds"], n_splits=5)
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "validation_protocol_train_only.json"), "w"), indent=1)
print(json.dumps(out, indent=1))
