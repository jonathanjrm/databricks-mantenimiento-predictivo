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
# MAGIC - Alias `champion` (no hay stages): se asigna solo si la version nueva mejora la **RMSE de
# MAGIC   validación por motor calculada solo con train** (`rmse_val_truncated`, GroupKFold de 5
# MAGIC   pliegues) del champion actual en más de la desviación entre pliegues, o si todavía no hay champion.
# MAGIC
# MAGIC Nota metodologica: el test de C-MAPSS es un banco de pruebas fijo y ya se ha usado para informar
# MAGIC resultados, así que no es independiente. Por eso el test **no interviene** en la decisión del
# MAGIC champion: solo se registra como métrica de referencia (`rmse_test`). Los hiperparametros se
# MAGIC eligieron con validación cruzada por motor solo sobre train (`scripts/entrenar_local.py --tune`).
# MAGIC Protocolo completo en `audit/02_MODEL_VALIDATION.md`, apartado B2.4.

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

# Workaround: en runtimes recientes leer ficheros normales del workspace puede fallar con
# "OSError: [Errno 5] Input/output error" (incidencia abierta en el foro de Databricks).
# Por eso el paquete `lib` se publica también en el volume `codigo` y tiene prioridad.
def _code_dirs():
    try:
        cat, sch = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
    except Exception:
        cat, sch = "workspace", "mantenimiento"
    return [f"/Volumes/{cat}/{sch}/codigo", _src_dir()]

for _d in reversed(_code_dirs()):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from lib.cmapss import RUL_CLIP, feature_columns  # noqa: E402
from lib.modeling import (HGB_PARAMS, evaluate, make_models, promotion_decision,  # noqa: E402
                          validation_rmse_by_engine)

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

train = (feat_df.filter("split = 'train'").select("unit", "cycle", "rul", *FEATS[1:]).toPandas()
         .sort_values(["unit", "cycle"]).reset_index(drop=True))
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
    # Métrica de selección: validación por motor SOLO con train (el test no participa).
    import sklearn
    val = validation_rmse_by_engine(lambda: make_models()[CANDIDATE], train, FEATS)
    cand.update({k: float(v) for k, v in val.items() if k.startswith("rmse_val") or k == "std_folds"})
    print(f"validación por motor (solo train): RMSE truncado={val['rmse_val_truncated']:.2f} "
          f"(desv. entre pliegues {val['std_folds']:.2f}), RMSE todos los ciclos={val['rmse_val_all_cycles']:.2f}")
    mlflow.log_metrics({**cand, "rmse_mejor_baseline": mejor_baseline})
    mlflow.log_params({"n_train_rows": len(X_train), "n_features": len(FEATS), "rul_clip": RUL_CLIP,
                       "sklearn_version": sklearn.__version__, "selection_metric": "rmse_val_truncated",
                       "validation_n_splits": val["n_splits"]})

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
# MAGIC Se compara la RMSE de **validación por motor (solo train)** de la version nueva con la del
# MAGIC champion actual (leída de `gold_model_metrics`, columna `rmse_val_truncated`). Solo una mejora
# MAGIC mayor que el error típico entre pliegues (`std_folds / sqrt(k)`) mueve el alias; el anterior queda disponible para
# MAGIC rollback reasignando el alias. Si el champion actual no tiene métrica de validación (versiones
# MAGIC anteriores a este protocolo), la versión nueva queda como `challenger` y la decisión es manual.
# MAGIC El RMSE de test se registra pero no decide.

# COMMAND ----------

def champion_actual():
    try:
        return client.get_model_version_by_alias(MODEL_NAME, "champion")
    except MlflowException:
        return None


def rmse_val_de_version(version: int):
    """RMSE de validación (solo train) registrada para una versión; None si no existe."""
    try:
        tabla = spark.table(f"{FQ}.gold_model_metrics")
        if "rmse_val_truncated" not in tabla.columns:
            return None
        fila = (tabla.filter(f"model_version = {int(version)} AND model_type = '{CANDIDATE}'")
                .orderBy("trained_at", ascending=False).select("rmse_val_truncated").first())
        return float(fila[0]) if fila and fila[0] is not None else None
    except Exception:  # la tabla aun no existe (primer entrenamiento)
        return None


champ = champion_actual()
promover, motivo = promotion_decision(
    None if champ is None else int(champ.version),
    None if champ is None else rmse_val_de_version(int(champ.version)),
    cand["rmse_val_truncated"], cand["std_folds"], n_splits=int(val["n_splits"]))

version_str = str(NEW_VERSION)
client.set_model_version_tag(MODEL_NAME, version_str, "rmse_test", f"{cand['rmse_test']:.4f}")
client.set_model_version_tag(MODEL_NAME, version_str, "rmse_val_truncated", f"{cand['rmse_val_truncated']:.4f}")
client.set_model_version_tag(MODEL_NAME, version_str, "selection_metric", "rmse_val_truncated (train only)")
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
    StructField("rmse_val_truncated", DoubleType()), StructField("rmse_val_all_cycles", DoubleType()),
    StructField("std_folds", DoubleType()),
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
                  float(m["rmse_val_truncated"]) if es_cand else None,
                  float(m["rmse_val_all_cycles"]) if es_cand else None,
                  float(m["std_folds"]) if es_cand else None,
                  int(RUL_CLIP), int(len(X_train)), int(len(FEATS)),
                  bool(promover) if es_cand else False, motivo if es_cand else "baseline"))
(spark.createDataFrame(filas, esquema).write.mode("append").option("mergeSchema", "true")
      .saveAsTable(f"{FQ}.gold_model_metrics"))
display(spark.table(f"{FQ}.gold_model_metrics").orderBy("trained_at", ascending=False).limit(10))
