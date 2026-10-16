# Databricks notebook source
# MAGIC %md
# MAGIC # 08. Register a validated serving model (audit finding D-02)
# MAGIC
# MAGIC The registered model (`rul_model`, logged by `02_train.py`) is a raw scikit-learn
# MAGIC `HistGradientBoostingRegressor`. It accepts NaN natively, so the serving endpoint returns a
# MAGIC number even for an all-null row instead of an error (see `audit/02_MODEL_VALIDATION.md`, D-02).
# MAGIC
# MAGIC This notebook wraps the **current `champion`** model in `lib.serving.ValidatedRULModel`
# MAGIC (schema + finite-value validation, same column set the batch job already enforces) and
# MAGIC registers the wrapper as a **new, separate model version**. It does **not** move the
# MAGIC `champion` alias: that stays a deliberate, later decision once this version has been
# MAGIC verified by real inference against the deployed endpoint.
# MAGIC
# MAGIC Run only with explicit authorization (writes a new Unity Catalog model version).

# COMMAND ----------

import os
import re
import sys


def _src_dir() -> str:
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        return os.path.dirname(nb if nb.startswith("/Workspace") else "/Workspace" + nb)
    except Exception:  # no notebook context
        return os.getcwd()


def _code_dirs() -> list[str]:
    try:
        cat, sch = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
    except Exception:
        cat, sch = "workspace", "maintenance"
    return [f"/Volumes/{cat}/{sch}/code", _src_dir()]


for _d in reversed(_code_dirs()):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from lib.cmapss import feature_columns
from lib.serving import log_validated_model

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "maintenance", "Schema")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"

# COMMAND ----------

import mlflow
import mlflow.sklearn
from mlflow import MlflowClient

mlflow.set_registry_uri("databricks-uc")
current_user = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{current_user}/predictive_maintenance")
client = MlflowClient()

champion = client.get_model_version_by_alias(MODEL_NAME, "champion")
print(f"Wrapping champion version {champion.version} of {MODEL_NAME}")
champion_model = mlflow.sklearn.load_model(f"models:/{MODEL_NAME}@champion")

# COMMAND ----------

# MAGIC %md ## Build an input example and log the wrapped candidate

# COMMAND ----------

FEATS = feature_columns()
example = (
    spark.table(f"{FQ}.gold_features")
    .filter("split = 'test'")
    .select(*FEATS)
    .limit(5)
    .toPandas()
    .astype("float64")
)
assert len(example) > 0, "no rows available to build a signature example"

with mlflow.start_run(run_name="register_validated_serving_model"):
    mlflow.set_tags({"wraps_champion_version": str(champion.version), "purpose": "D-02 input validation"})
    info = log_validated_model(
        champion_model,
        FEATS,
        input_example=example,
        registered_model_name=MODEL_NAME,
    )
new_version = info.registered_model_version
print(f"Registered candidate version {new_version} of {MODEL_NAME} (champion alias NOT moved)")

# COMMAND ----------

# MAGIC %md ## Verify the new version with real inference before proposing promotion

# COMMAND ----------

loaded = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}/{new_version}")

# Valid input must match the wrapped champion exactly.
good_pred = loaded.predict(example)
champion_pred = champion_model.predict(example[FEATS])
import numpy as np

assert np.allclose(np.asarray(good_pred), champion_pred), "wrapped model diverges from champion"
print("Valid input: predictions match the champion model exactly.")

# Invalid input must raise, not return a number.
bad = example.copy()
bad.loc[0, :] = float("nan")
try:
    loaded.predict(bad)
    raise AssertionError("expected the validated model to reject an all-NaN row")
except AssertionError:
    raise
except Exception as e:
    print(f"All-NaN row correctly rejected: {type(e).__name__}: {e}")

print(
    f"\nCandidate version {new_version} verified locally via mlflow.pyfunc.load_model.\n"
    "Next steps (manual, separate authorization each):\n"
    f"  1. Deploy a serving endpoint pointing at models:/{MODEL_NAME}/{new_version} "
    "(or update the existing endpoint's served entity) and repeat this same "
    "valid/invalid check against the REST API, not just the local load.\n"
    "  2. Only after that live check passes, decide whether to move the "
    "`champion` alias; this script intentionally never does that automatically."
)
