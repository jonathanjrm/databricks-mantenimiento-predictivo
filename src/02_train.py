# Databricks notebook source
# MAGIC %md
# MAGIC # 02. RUL model training
# MAGIC
# MAGIC Trains a remaining useful life (RUL) model with scikit-learn on `gold_features`, compares it
# MAGIC with two baselines, evaluates it on the **last cycle of every test engine** against the actual
# MAGIC RUL and registers it in Unity Catalog (`<catalog>.<schema>.rul_model`) with MLflow 3.
# MAGIC
# MAGIC - Candidate: `HistGradientBoostingRegressor`. Baselines: constant mean and Ridge regression.
# MAGIC - Metrics: RMSE and the asymmetric PHM08 score (Saxena et al., 2008), which penalises late
# MAGIC   predictions more than early ones.
# MAGIC - `champion` alias (there are no stages): assigned only if the new version improves the
# MAGIC   **validation RMSE by engine computed on train only** (`rmse_val_truncated`, 5-fold GroupKFold)
# MAGIC   of the current champion by more than the fold-to-fold deviation, or if there is no champion yet.
# MAGIC
# MAGIC Methodological note: the C-MAPSS test set is a fixed benchmark that has already been used to
# MAGIC report results, so it is not independent. That is why the test set **does not take part** in the
# MAGIC champion decision: it is only recorded as a reference metric (`rmse_test`). Hyperparameters were
# MAGIC chosen with cross-validation by engine on train only (`scripts/train_local.py --tune`).
# MAGIC Full protocol in `audit/02_MODEL_VALIDATION.md`, section B2.4.

# COMMAND ----------

import os
import re
import sys
from datetime import datetime, timezone


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

from lib.cmapss import RUL_CLIP, feature_columns
from lib.modeling import (
    CANDIDATE_MODEL,
    HGB_PARAMS,
    evaluate,
    make_models,
    promotion_decision,
    validation_rmse_by_engine,
)

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "maintenance", "Schema")
dbutils.widgets.text("experiment_path", "", "MLflow experiment (empty = /Users/<you>/predictive_maintenance)")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"
N_TEST_ENGINES = 100
SIGNATURE_ROWS = 500  # rows used to infer the model signature

# COMMAND ----------

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from mlflow.models import infer_signature

mlflow.set_registry_uri("databricks-uc")  # models in Unity Catalog (no stages)
current_user = spark.sql("SELECT current_user()").first()[0]
exp_path = dbutils.widgets.get("experiment_path").strip() or f"/Users/{current_user}/predictive_maintenance"
mlflow.set_experiment(exp_path)
client = MlflowClient()
print("Model:", MODEL_NAME, "| experiment:", exp_path)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Data from gold
# MAGIC Converted to pandas: about 34,000 rows fit comfortably in memory and scikit-learn works with pandas.

# COMMAND ----------

FEATS = feature_columns()
feat_df = spark.table(f"{FQ}.gold_features")
missing = [c for c in FEATS if c not in feat_df.columns]
assert not missing, f"Missing columns in gold_features: {missing[:5]}"

train = (
    feat_df.filter("split = 'train'")
    .select("unit", "cycle", "rul", *FEATS[1:])
    .toPandas()
    .sort_values(["unit", "cycle"])
    .reset_index(drop=True)
)
test_all = feat_df.filter("split = 'test'").select("unit", "cycle", *FEATS[1:]).toPandas()
truth = spark.table(f"{FQ}.gold_test_truth").select("unit", "last_cycle", "rul_real").toPandas()

test = (
    test_all.merge(truth, left_on=["unit", "cycle"], right_on=["unit", "last_cycle"], how="inner")
    .sort_values("unit")
    .reset_index(drop=True)
)
assert len(test) == N_TEST_ENGINES and test["rul_real"].notna().all(), (
    f"expected {N_TEST_ENGINES} test engines with an actual RUL"
)
X_train, y_train = train[FEATS].astype("float64"), train["rul"].astype("float64")
X_test, y_test = test[FEATS].astype("float64"), test["rul_real"].astype("float64")
print("train:", X_train.shape, "| test (last cycle):", X_test.shape)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Train and evaluate (candidate and baselines)

# COMMAND ----------

import sklearn

results, models = {}, {}
with mlflow.start_run(run_name="rul_training") as parent:
    mlflow.set_tags(
        {"dataset": "NASA C-MAPSS FD001", "rul_clip": str(RUL_CLIP), "split_eval": "test_last_cycle"}
    )
    for name, model in make_models().items():
        with mlflow.start_run(run_name=name, nested=True):
            model.fit(X_train, y_train)
            m = evaluate(model, X_test, y_test)
            mlflow.log_metrics(m)
            mlflow.log_param("model_type", name)
            if name == CANDIDATE_MODEL:
                mlflow.log_params({k: str(v) for k, v in HGB_PARAMS.items()})
        results[name], models[name] = m, model
        print(f"{name:26s} RMSE={m['rmse_test']:.2f}  PHM08={m['phm08_test']:.0f}  MAE={m['mae_test']:.2f}")

    best_baseline = min(results[k]["rmse_test"] for k in results if k != CANDIDATE_MODEL)
    cand = results[CANDIDATE_MODEL]
    # Selection metric: validation by engine on train ONLY (the test set does not take part).
    val = validation_rmse_by_engine(lambda: make_models()[CANDIDATE_MODEL], train, FEATS)
    cand.update({k: float(v) for k, v in val.items() if k.startswith("rmse_val") or k == "std_folds"})
    print(
        f"validation by engine (train only): truncated RMSE={val['rmse_val_truncated']:.2f} "
        f"(fold std {val['std_folds']:.2f}), all-cycles RMSE={val['rmse_val_all_cycles']:.2f}"
    )
    mlflow.log_metrics({**cand, "rmse_best_baseline": best_baseline})
    mlflow.log_params(
        {
            "n_train_rows": len(X_train),
            "n_features": len(FEATS),
            "rul_clip": RUL_CLIP,
            "sklearn_version": sklearn.__version__,
            "selection_metric": "rmse_val_truncated",
            "validation_n_splits": val["n_splits"],
        }
    )

    # MLflow 3: `name` (not artifact_path); signature and input_example are mandatory in UC.
    candidate = models[CANDIDATE_MODEL]
    signature = infer_signature(X_train.head(SIGNATURE_ROWS), candidate.predict(X_train.head(SIGNATURE_ROWS)))
    info = mlflow.sklearn.log_model(
        sk_model=candidate,
        name="model",
        signature=signature,
        input_example=X_train.head(5),
        registered_model_name=MODEL_NAME,
    )
    NEW_VERSION = int(info.registered_model_version)
    RUN_ID = parent.info.run_id
print("Registered version:", MODEL_NAME, "v", NEW_VERSION)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. `champion` alias decision
# MAGIC The **validation RMSE by engine (train only)** of the new version is compared with the current
# MAGIC champion's (read from `gold_model_metrics`, column `rmse_val_truncated`). Only an improvement
# MAGIC larger than the standard error across folds (`std_folds / sqrt(k)`) moves the alias; the previous
# MAGIC version stays available for rollback by reassigning the alias. If the current champion has no
# MAGIC validation metric (versions older than this protocol), the new version becomes `challenger` and
# MAGIC the decision is manual. The test RMSE is recorded but never decides.

# COMMAND ----------


def current_champion():
    try:
        return client.get_model_version_by_alias(MODEL_NAME, "champion")
    except MlflowException:
        return None


def validation_rmse_of_version(version: int) -> float | None:
    """Validation RMSE (train only) recorded for a version; None if it does not exist."""
    try:
        table = spark.table(f"{FQ}.gold_model_metrics")
        if "rmse_val_truncated" not in table.columns:
            return None
        row = (
            table.filter(f"model_version = {int(version)} AND model_type = '{CANDIDATE_MODEL}'")
            .orderBy("trained_at", ascending=False)
            .select("rmse_val_truncated")
            .first()
        )
        return float(row[0]) if row and row[0] is not None else None
    except Exception:  # the table does not exist yet (first training)
        return None


champion = current_champion()
promote, reason = promotion_decision(
    None if champion is None else int(champion.version),
    None if champion is None else validation_rmse_of_version(int(champion.version)),
    cand["rmse_val_truncated"],
    cand["std_folds"],
    n_splits=int(val["n_splits"]),
)

version_str = str(NEW_VERSION)
client.set_model_version_tag(MODEL_NAME, version_str, "rmse_test", f"{cand['rmse_test']:.4f}")
client.set_model_version_tag(
    MODEL_NAME, version_str, "rmse_val_truncated", f"{cand['rmse_val_truncated']:.4f}"
)
client.set_model_version_tag(MODEL_NAME, version_str, "selection_metric", "rmse_val_truncated (train only)")
client.set_model_version_tag(MODEL_NAME, version_str, "dataset", "C-MAPSS FD001")
if promote:
    client.set_registered_model_alias(MODEL_NAME, "champion", NEW_VERSION)
else:
    client.set_registered_model_alias(MODEL_NAME, "challenger", NEW_VERSION)
print(("PROMOTED to champion: " if promote else "Stays as challenger: ") + reason)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Metrics in `gold_model_metrics`
# MAGIC One row per model and training run (accumulated history). Baseline versions are not registered
# MAGIC in Unity Catalog, so `model_version` is null for them.

# COMMAND ----------

from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

metrics_schema = StructType(
    [
        StructField("trained_at", TimestampType()),
        StructField("run_id", StringType()),
        StructField("model_name", StringType()),
        StructField("model_version", IntegerType()),
        StructField("model_type", StringType()),
        StructField("rmse_test", DoubleType()),
        StructField("phm08_test", DoubleType()),
        StructField("mae_test", DoubleType()),
        StructField("rmse_val_truncated", DoubleType()),
        StructField("rmse_val_all_cycles", DoubleType()),
        StructField("std_folds", DoubleType()),
        StructField("rul_clip", IntegerType()),
        StructField("n_train_rows", LongType()),
        StructField("n_features", IntegerType()),
        StructField("promoted_to_champion", BooleanType()),
        StructField("decision", StringType()),
    ]
)
now = datetime.now(timezone.utc)
rows = []
for name, m in results.items():
    is_cand = name == CANDIDATE_MODEL
    rows.append(
        (
            now,
            RUN_ID,
            MODEL_NAME,
            NEW_VERSION if is_cand else None,
            name,
            float(m["rmse_test"]),
            float(m["phm08_test"]),
            float(m["mae_test"]),
            float(m["rmse_val_truncated"]) if is_cand else None,
            float(m["rmse_val_all_cycles"]) if is_cand else None,
            float(m["std_folds"]) if is_cand else None,
            int(RUL_CLIP),
            len(X_train),
            len(FEATS),
            bool(promote) if is_cand else False,
            reason if is_cand else "baseline",
        )
    )
(
    spark.createDataFrame(rows, metrics_schema)
    .write.mode("append")
    .option("mergeSchema", "true")
    .saveAsTable(f"{FQ}.gold_model_metrics")
)
display(spark.table(f"{FQ}.gold_model_metrics").orderBy("trained_at", ascending=False).limit(10))
