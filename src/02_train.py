# Databricks notebook source
# MAGIC %md
# MAGIC # 02. Entrenamiento del modelo de RUL
# MAGIC
# MAGIC Entrena un modelo de vida útil restante (RUL) con scikit-learn sobre `gold_features`, lo compara
# MAGIC con dos baselines, lo evalua en el **último ciclo de cada motor de test** contra la RUL real y lo
# MAGIC registra en Unity Catalog (`<catálogo>.<schema>.rul_model`) con MLflow 3.
# MAGIC
# MAGIC - Candidato: `HistGradientBoostingRegressor`. Baselines: media constante y regresion Ridge.
# MAGIC - Metricas: RMSE y la score asimetrica de PHM08 (Saxena et al., 2008), que penaliza mas las
# MAGIC   predicciones tardias que las tempranas.
# MAGIC - Alias `champion` (no hay stages): se asigna solo si la version nueva mejora el RMSE de test
# MAGIC   del champion actual, o si todavia no hay champion.
# MAGIC
# MAGIC Nota metodologica: el test de C-MAPSS es un banco de pruebas fijo. Los hiperparametros se
# MAGIC eligieron con validación cruzada por motor solo sobre train (`scripts/entrenar_local.py --tune`),
# MAGIC pero decidir el champion con el mismo test es una puerta de calidad, no una estimación
# MAGIC independiente del error futuro.

# COMMAND ----------

import os
import re
import sys

import numpy as np
import pandas as pd

def _src_dir():
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        return os.path.dirname(nb if nb.startswith("/Workspace") else "/Workspace" + nb)
    except Exception:  # verificar en docs
        return os.getcwd()

sys.path.insert(0, _src_dir())  # para poder importar el paquete `lib` (src/lib)

from lib.cmapss import RUL_CLIP, feature_columns  # noqa: E402
from lib.modeling import HGB_PARAMS, evaluate, make_models  # noqa: E402

dbutils.widgets.text("catalog", "workspace", "Catálogo")
dbutils.widgets.text("schema", "mantenimiento", "Schema")
dbutils.widgets.text("experiment_path", "", "Experimento MLflow (vacio = /Users/<tu usuario>/mantenimiento_predictivo)")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Nombre no valido: {_n!r}")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"
CANDIDATE = "hist_gradient_boosting"

# COMMAND ----------

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException
from mlflow.models import infer_signature

mlflow.set_registry_uri("databricks-uc")  # modelos en Unity Catalog (nada de stages)
usuario = spark.sql("SELECT current_user()").first()[0]
exp_path = dbutils.widgets.get("experiment_path").strip() or f"/Users/{usuario}/mantenimiento_predictivo"
mlflow.set_experiment(exp_path)
client = MlflowClient()
print("Modelo:", MODEL_NAME, "| experimento:", exp_path)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Datos desde gold
# MAGIC Se pasan a pandas: son ~34.000 filas, caben de sobra en memoria y scikit-learn trabaja con pandas.

# COMMAND ----------

FEATS = feature_columns()
feat_df = spark.table(f"{FQ}.gold_features")
faltan = [c for c in FEATS if c not in feat_df.columns]
assert not faltan, f"Faltan columnas en gold_features: {faltan[:5]}"

train = feat_df.filter("split = 'train'").select("unit", "cycle", "rul", *FEATS[1:]).toPandas()
test_all = feat_df.filter("split = 'test'").select("unit", "cycle", *FEATS[1:]).toPandas()
verdad = spark.table(f"{FQ}.gold_test_verdad").select("unit", "cycle_final", "rul_real").toPandas()

test = (test_all.merge(verdad, left_on=["unit", "cycle"], right_on=["unit", "cycle_final"], how="inner")
        .sort_values("unit").reset_index(drop=True))
assert len(test) == 100 and test["rul_real"].notna().all(), "se esperaban 100 motores de test con RUL real"
X_train, y_train = train[FEATS].astype("float64"), train["rul"].astype("float64")
X_test, y_test = test[FEATS].astype("float64"), test["rul_real"].astype("float64")
print("train:", X_train.shape, "| test (último ciclo):", X_test.shape)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Entrenar y evaluar (candidato y baselines)

# COMMAND ----------

resultados, modelos = {}, {}
with mlflow.start_run(run_name="rul_entrenamiento") as parent:
    mlflow.set_tags({"dataset": "NASA C-MAPSS FD001", "rul_clip": str(RUL_CLIP), "split_eval": "test_ultimo_ciclo"})
    for nombre, modelo in make_models().items():
        with mlflow.start_run(run_name=nombre, nested=True):
            modelo.fit(X_train, y_train)
            m = evaluate(modelo, X_test, y_test)
            mlflow.log_metrics(m)
            mlflow.log_param("model_type", nombre)
            if nombre == CANDIDATE:
                mlflow.log_params({k: str(v) for k, v in HGB_PARAMS.items()})
        resultados[nombre], modelos[nombre] = m, modelo
        print(f"{nombre:26s} RMSE={m['rmse_test']:.2f}  PHM08={m['phm08_test']:.0f}  MAE={m['mae_test']:.2f}")

    mejor_baseline = min(resultados[k]["rmse_test"] for k in resultados if k != CANDIDATE)
    cand = resultados[CANDIDATE]
    mlflow.log_metrics({**cand, "rmse_mejor_baseline": mejor_baseline})
    mlflow.log_params({"n_train_rows": len(X_train), "n_features": len(FEATS), "rul_clip": RUL_CLIP})

    # MLflow 3: `name` (no artifact_path), signature e input_example obligatorios en UC.
    candidato = modelos[CANDIDATE]
    firma = infer_signature(X_train.head(500), candidato.predict(X_train.head(500)))
    info = mlflow.sklearn.log_model(
        sk_model=candidato,
        name="model",
        signature=firma,
        input_example=X_train.head(5),
        registered_model_name=MODEL_NAME,
    )
    NEW_VERSION = int(info.registered_model_version)
    RUN_ID = parent.info.run_id
print("Version registrada:", MODEL_NAME, "v", NEW_VERSION)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Decisión del alias `champion`
# MAGIC Se compara el RMSE de test de la version nueva con el del champion actual (leido de
# MAGIC `gold_model_metrics`). Solo mejora estricta mueve el alias; el anterior queda disponible
# MAGIC para rollback reasignando el alias.

# COMMAND ----------

def champion_actual():
    try:
        return client.get_model_version_by_alias(MODEL_NAME, "champion")
    except MlflowException:
        return None


def rmse_de_version(version: int):
    try:
        fila = (spark.table(f"{FQ}.gold_model_metrics")
                .filter(f"model_version = {int(version)} AND model_type = '{CANDIDATE}'")
                .orderBy("trained_at", ascending=False).select("rmse_test").first())
        return float(fila[0]) if fila else None
    except Exception:  # la tabla aun no existe (primer entrenamiento)
        return None


champ = champion_actual()
if champ is None:
    promover, motivo = True, "no habia champion: se asigna a la primera version"
else:
    rmse_champ = rmse_de_version(int(champ.version))
    if rmse_champ is None:
        promover, motivo = True, f"sin métricas registradas del champion v{champ.version}: se reemplaza"
    elif cand["rmse_test"] < rmse_champ:
        promover, motivo = True, f"mejora el RMSE de test: {cand['rmse_test']:.3f} < {rmse_champ:.3f} (champion v{champ.version})"
    else:
        promover, motivo = False, f"no mejora: {cand['rmse_test']:.3f} >= {rmse_champ:.3f} (champion v{champ.version})"

version_str = str(NEW_VERSION)
client.set_model_version_tag(MODEL_NAME, version_str, "rmse_test", f"{cand['rmse_test']:.4f}")
client.set_model_version_tag(MODEL_NAME, version_str, "dataset", "C-MAPSS FD001")
if promover:
    client.set_registered_model_alias(MODEL_NAME, "champion", NEW_VERSION)
else:
    client.set_registered_model_alias(MODEL_NAME, "challenger", NEW_VERSION)
print(("PROMOVIDO a champion: " if promover else "Queda como challenger: ") + motivo)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Metricas en `gold_model_metrics`
# MAGIC Una fila por modelo y entrenamiento (histórico acumulado). Las versiones de los baselines
# MAGIC no se registran en Unity Catalog, por eso `model_version` es nulo en ellas.

# COMMAND ----------

from datetime import datetime, timezone
from pyspark.sql.types import (BooleanType, DoubleType, IntegerType, LongType, StringType,
                               StructField, StructType, TimestampType)

esquema = StructType([
    StructField("trained_at", TimestampType()), StructField("run_id", StringType()),
    StructField("model_name", StringType()), StructField("model_version", IntegerType()),
    StructField("model_type", StringType()), StructField("rmse_test", DoubleType()),
    StructField("phm08_test", DoubleType()), StructField("mae_test", DoubleType()),
    StructField("rul_clip", IntegerType()), StructField("n_train_rows", LongType()),
    StructField("n_features", IntegerType()), StructField("promoted_to_champion", BooleanType()),
    StructField("decisión", StringType()),
])
ahora = datetime.now(timezone.utc)
filas = []
for nombre, m in resultados.items():
    es_cand = nombre == CANDIDATE
    filas.append((ahora, RUN_ID, MODEL_NAME, NEW_VERSION if es_cand else None, nombre,
                  float(m["rmse_test"]), float(m["phm08_test"]), float(m["mae_test"]),
                  int(RUL_CLIP), int(len(X_train)), int(len(FEATS)),
                  bool(promover) if es_cand else False, motivo if es_cand else "baseline"))
(spark.createDataFrame(filas, esquema).write.mode("append").option("mergeSchema", "true")
      .saveAsTable(f"{FQ}.gold_model_metrics"))
display(spark.table(f"{FQ}.gold_model_metrics").orderBy("trained_at", ascending=False).limit(10))
