"""B1: compara las tablas del workspace (consultas SQL guardadas en workspace/) con el calculo pandas local.
Uso: python compare_workspace_vs_local.py
"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
import numpy as np, pandas as pd
from lib.cmapss import (read_cmapss, read_rul, add_rul_train, rolling_features, feature_columns, last_cycle,
                        ACTIVE_SENSORS, CONSTANT_SENSORS, CONSTANT_SETTINGS, find_constant_columns, SENSORS, SETTINGS, COLUMNS)

def tbl(name):
    d = json.load(open(os.path.join(HERE, "workspace", name + ".json")))
    cols = [c["name"] for c in d["manifest"]["schema"]["columns"]]
    df = pd.DataFrame(d["result"]["data_array"], columns=cols)
    for c in df.columns:
        try: df[c] = pd.to_numeric(df[c])
        except Exception: pass
    return df

D = os.path.join(ROOT, "data", "cmapss")
tr = read_cmapss(f"{D}/train_FD001.txt"); te = read_cmapss(f"{D}/test_FD001.txt"); rul = read_rul(f"{D}/RUL_FD001.txt")
out = {}
# Integridad de los ficheros
out["files"] = {"train_rows": len(tr), "train_units": int(tr.unit.nunique()), "test_rows": len(te), "test_units": int(te.unit.nunique()),
                "rul_values": len(rul), "official_fd001": {"train_rows": 20631, "train_units": 100, "test_rows": 13096, "test_units": 100, "rul_values": 100}}
out["files"]["train_cycles_contiguous"] = bool((tr.groupby("unit").cycle.apply(lambda s: (s.values == np.arange(1, len(s) + 1)).all())).all())
out["files"]["test_cycles_contiguous"] = bool((te.groupby("unit").cycle.apply(lambda s: (s.values == np.arange(1, len(s) + 1)).all())).all())
out["files"]["nulls"] = int(tr.isna().sum().sum() + te.isna().sum().sum())
out["files"]["unit_ids_train"] = [int(tr.unit.min()), int(tr.unit.max())]; out["files"]["unit_ids_test"] = [int(te.unit.min()), int(te.unit.max())]
# Constantes
allx = pd.concat([tr, te])
const_tr = find_constant_columns(tr, SENSORS + SETTINGS); const_te = find_constant_columns(te, SENSORS + SETTINGS)
out["constant_columns"] = {"train": const_tr, "test": const_te, "declared_dropped": CONSTANT_SENSORS + CONSTANT_SETTINGS,
                           "s6_distinct_values": sorted(allx.s6.unique().tolist()), "s6_std": float(allx.s6.std(ddof=0)),
                           "kept_sensor_stds_train_min": float(tr[ACTIVE_SENSORS].std(ddof=0).min()),
                           "setting1_std": float(tr.setting1.std()), "setting2_std": float(tr.setting2.std())}
# RUL
trl = add_rul_train(tr)
out["rul_label"] = {"rul_raw_min": int(trl.rul_raw.min()), "rul_max_clipped": int(trl.rul.max()),
                    "last_cycle_has_rul0": bool((trl[trl.cycle == trl.groupby("unit").cycle.transform("max")].rul_raw == 0).all()),
                    "share_rows_clipped": float((trl.rul_raw > 125).mean()),
                    "test_final_cycle_plus_rul_real_equals_failure_cycle_example": {"unit1_last_cycle": int(te[te.unit == 1].cycle.max()), "unit1_rul_real": int(rul.rul_real[0])}}
# Features locales
F = feature_columns()
trf = rolling_features(trl); tef = rolling_features(te)
# 1) sumas por columna vs Spark (gold_features)
s = tbl("sql_gold_features_column_sums").set_index("split")
rows = []
for split, df in (("train", trf), ("test", tef)):
    for c in F[1:] + ["cycle"]:
        a = float(df[c].sum()); b = float(s.loc[split, c]); rel = abs(a - b) / max(1.0, abs(a))
        rows.append((split, c, a, b, rel))
cmp = pd.DataFrame(rows, columns=["split", "col", "pandas_sum", "spark_sum", "rel_diff"])
cmp.to_csv(os.path.join(HERE, "features_sums_pandas_vs_workspace.csv"), index=False)
out["feature_sums_pandas_vs_gold_features"] = {"n_cols_compared": len(cmp), "max_rel_diff": float(cmp.rel_diff.max()), "all_below_1e-8": bool((cmp.rel_diff < 1e-8).all()),
    "n_rows": {"train": int(s.loc["train", "n"]), "test": int(s.loc["test", "n"])}}
out["rul_sums"] = {"train_rul_pandas": float(trl.rul.sum()), "train_rul_gold": float(s.loc["train", "rul"]), "train_rul_raw_pandas": float(trl.rul_raw.sum()), "train_rul_raw_gold": float(s.loc["train", "rul_raw"])}
# 2) ultima fila de test por motor, 85 variables, fila a fila
q1 = tbl("sql_gold_features_test_last_cycle_85feats").sort_values("unit").reset_index(drop=True)
loc = last_cycle(tef).merge(rul, on="unit")
diff = np.abs(q1[F].to_numpy(float) - loc[F].to_numpy(float))
out["last_cycle_rows_100_engines"] = {"max_abs_diff_any_feature": float(diff.max()), "units_equal": bool((q1.unit.values == loc.unit.values).all()),
                                      "rul_real_equal": bool((q1.rul_real.values == loc.rul_real.values).all()),
                                      "rul_real_equal_official_file": bool((q1.rul_real.values == rul.rul_real.values).all())}
# 3) bronze/silver sumas
sv = tbl("sql_silver_column_sums").set_index("split")
out["silver_sums_vs_raw_files"] = {sp: {c: float(abs(float(sv.loc[sp, c]) - float(df[c].sum()))) for c in ["s2", "s7", "s11", "s21", "setting1", "setting2"]} for sp, df in (("train", tr), ("test", te))}
out["silver_rul_test_vs_file"] = None
r8 = tbl("sql_silver_rul_test"); r8c = [c for c in r8.columns]
out["silver_rul_test_columns"] = r8c
if "rul_real" in r8.columns:
    out["silver_rul_test_vs_file"] = bool((r8.sort_values("unit").rul_real.values == rul.rul_real.values).all())
# 4) test de ausencia de futuro: truncar el test en el ciclo c y comprobar que las variables del ciclo c no cambian
rng = np.random.default_rng(0); bad = 0; n = 0
for u in (1, 17, 42, 88, 100):
    d = tr[tr.unit == u]
    full = rolling_features(d).set_index("cycle")
    for cut in rng.choice(np.arange(5, len(d)), 8, replace=False):
        part = rolling_features(d[d.cycle <= cut]).set_index("cycle")
        a = full.loc[cut, F[1:]].to_numpy(float); b = part.loc[cut, F[1:]].to_numpy(float)
        n += 1; bad += int(not np.allclose(a, b, rtol=0, atol=1e-12))
out["no_future_leakage_truncation_test"] = {"checks": n, "mismatches": bad}
# 5) separacion train/test
out["train_test_separation"] = {"gold_features_split_values": ["train", "test"], "source_files": tbl("sql_silver_source_files").to_dict("records"),
    "rows_identical_between_train_and_test_files": int(len(pd.merge(tr.drop(columns=["unit", "cycle"]).drop_duplicates(), te.drop(columns=["unit", "cycle"]).drop_duplicates())))}
# 6) predicciones (gold_predicciones) vs verdad
gp = tbl("sql_gold_predicciones").sort_values("unit").reset_index(drop=True)
out["gold_predicciones"] = {"rows": len(gp), "units_1_100": bool((gp.unit.values == np.arange(1, 101)).all()), "model_versions": sorted(gp.model_version.unique().tolist()),
    "rul_real_equal_official": bool((gp.rul_real.values == rul.rul_real.values).all()), "cycle_equals_last_cycle_test": bool((gp.cycle.values == te.groupby("unit").cycle.max().values).all()),
    "rul_pred_distinct_values": int(gp.rul_pred.nunique()), "rul_pred_min": float(gp.rul_pred.min()), "rul_pred_max": float(gp.rul_pred.max()),
    "error_equals_pred_minus_real": bool(np.allclose(gp.error, gp.rul_pred - gp.rul_real, atol=0.011))}
json.dump(out, open(os.path.join(HERE, "b1_data_integrity.json"), "w"), indent=2, default=str)
print(json.dumps(out, indent=1, default=str)[:6000])
