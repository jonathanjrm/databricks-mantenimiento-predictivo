# Databricks notebook source
# MAGIC %md
# MAGIC # 03. Fleet scoring
# MAGIC
# MAGIC Loads `models:/<catalog>.<schema>.rul_model@champion`, scores the **last observed cycle** of the
# MAGIC 100 test engines and writes `gold_predictions`: estimated RUL, actual RUL, state
# MAGIC (critical, watch, healthy) and the 3 sensors that explain the prediction most.
# MAGIC
# MAGIC ## How the top 3 sensors are computed (and what they are NOT)
# MAGIC For every engine and sensor, **all** the features derived from that sensor (value, rolling
# MAGIC means and standard deviations, slope) are replaced by their typical healthy value (median of
# MAGIC the first 30 cycles of the train engines) and the model predicts again. The contribution is
# MAGIC `RUL_without_the_sensor - RUL_original`: how much remaining life that sensor "would give back"
# MAGIC if it were healthy. Positive = the sensor pushes the RUL down.
# MAGIC
# MAGIC State and action text are **advisory**: demo thresholds on a statistical estimate without
# MAGIC calibrated intervals; every operational decision requires engineering review.
# MAGIC
# MAGIC This is a **local occlusion sensitivity**, not SHAP values: contributions do not add up to the
# MAGIC prediction, the model can respond unreliably to unrealistic combinations (one healthy sensor
# MAGIC with the others degraded) and correlated sensors share the effect arbitrarily. It helps to
# MAGIC prioritise what to look at, not to prove causality.

# COMMAND ----------

import os
import re
import sys

import numpy as np
import pandas as pd


def _src_dir() -> str:
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        return os.path.dirname(nb if nb.startswith("/Workspace") else "/Workspace" + nb)
    except Exception:  # no notebook context
        return os.getcwd()


# Workaround: on recent runtimes reading plain workspace files can fail with
# "OSError: [Errno 5] Input/output error" (open issue in the Databricks forum).
# The `lib` package is therefore also published to the `code` volume, which takes priority.
def _code_dirs() -> list[str]:
    try:
        cat, sch = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
    except Exception:
        cat, sch = "workspace", "maintenance"
    return [f"/Volumes/{cat}/{sch}/code", _src_dir()]


for _d in reversed(_code_dirs()):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from lib.cmapss import (
    ACTIVE_SENSORS,
    HEALTHY_CYCLES,
    classify_state,
    feature_columns,
    occlusion_contributions,
    phm08_score,
    recommended_action,
    rmse,
    top_k_sensors,
    validate_feature_frame,
)

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "maintenance", "Schema")
dbutils.widgets.text("critical_threshold", "20", "RUL <= threshold: critical")
dbutils.widgets.text("watch_threshold", "35", "RUL <= threshold: watch")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")
CRITICAL_THRESHOLD = float(dbutils.widgets.get("critical_threshold"))
WATCH_THRESHOLD = float(dbutils.widgets.get("watch_threshold"))
assert 0 <= CRITICAL_THRESHOLD < WATCH_THRESHOLD, "thresholds must satisfy 0 <= critical < watch"
FQ = f"`{CATALOG}`.`{SCHEMA}`"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"
N_TEST_ENGINES = 100
TOP_SENSORS = 3

# COMMAND ----------

import mlflow
from mlflow import MlflowClient

mlflow.set_registry_uri("databricks-uc")
version = MlflowClient().get_model_version_by_alias(
    MODEL_NAME, "champion"
)  # fails clearly without a champion
model = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@champion")
print("Champion:", MODEL_NAME, "version", version.version)

# COMMAND ----------

FEATS = feature_columns()
feat = spark.table(f"{FQ}.gold_features")
truth = spark.table(f"{FQ}.gold_test_truth").select("unit", "last_cycle", "rul_real")

test = (
    feat.filter("split = 'test'")
    .select("unit", "cycle", *FEATS[1:])
    .join(truth, (feat.unit == truth.unit) & (feat.cycle == truth.last_cycle))
    .select(feat.unit, feat.cycle, "rul_real", *FEATS[1:])
    .toPandas()
    .sort_values("unit")
    .reset_index(drop=True)
)
assert len(test) == N_TEST_ENGINES, f"expected {N_TEST_ENGINES} engines, got {len(test)}"
X = test[FEATS].astype("float64")
validate_feature_frame(X, FEATS)  # explicit error on null/inf/missing sensors; no silent prediction

# Healthy reference: median of the features over the first 30 cycles of the train engines.
healthy = (
    feat.filter(f"split = 'train' AND cycle <= {HEALTHY_CYCLES}")
    .select(*FEATS)
    .toPandas()
    .astype("float64")
    .median()
)

# COMMAND ----------

predict = lambda d: np.asarray(model.predict(d[FEATS]), dtype=float)
pred = np.clip(predict(X), 0.0, None)
contrib = occlusion_contributions(predict, X, healthy, sensors=ACTIVE_SENSORS)
top = top_k_sensors(contrib, k=TOP_SENSORS)

out = pd.DataFrame(
    {
        "unit": test["unit"].astype(int),
        "cycle": test["cycle"].astype(int),
        "rul_pred": pred.round(2),
        "rul_real": test["rul_real"].astype(int),
    }
)
out["error"] = (out["rul_pred"] - out["rul_real"]).round(2)
out["state"] = [classify_state(r, CRITICAL_THRESHOLD, WATCH_THRESHOLD) for r in out["rul_pred"]]
for i in range(TOP_SENSORS):
    out[f"sensor_{i + 1}"] = [t[i][0] for t in top]
    out[f"contrib_{i + 1}"] = [round(t[i][1], 3) for t in top]
out["action"] = [recommended_action(r.state, r.rul_pred, [r.sensor_1, r.sensor_2]) for r in out.itertuples()]
out["critical_threshold"] = CRITICAL_THRESHOLD
out["watch_threshold"] = WATCH_THRESHOLD
out["model_name"] = MODEL_NAME
out["model_version"] = int(version.version)

print(
    f"RMSE vs actual RUL: {rmse(out.rul_real, out.rul_pred):.2f} | "
    f"PHM08: {phm08_score(out.rul_real, out.rul_pred):.0f}"
)
print(out["state"].value_counts().to_string())

# COMMAND ----------

from pyspark.sql import functions as F

sdf = spark.createDataFrame(out).withColumn("scored_at", F.current_timestamp())
sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gold_predictions")
display(spark.table(f"{FQ}.gold_predictions").orderBy("rul_pred").limit(15))
