# Databricks notebook source
# MAGIC %md
# MAGIC # 03. Puntuacion de la flota
# MAGIC
# MAGIC Carga `models:/<catálogo>.<schema>.rul_model@champion`, puntua el **último ciclo observado** de los
# MAGIC 100 motores de test y escribe `gold_predicciones`: RUL estimada, RUL real, estado
# MAGIC (critical, watch, healthy) y los 3 sensores que mas explican la predicción.
# MAGIC
# MAGIC ## Como se calculan los 3 sensores principales (y que NO es)
# MAGIC Para cada motor y cada sensor se sustituyen **todas** las variables derivadas de ese sensor (valor,
# MAGIC medias y desviaciones moviles, pendiente) por su valor típico en la fase sana (mediana de los
# MAGIC primeros 30 ciclos de los motores de train) y se vuelve a predecir. La contribución es
# MAGIC `RUL_sin_el_sensor - RUL_original`: cuanta vida restante "devolveria" ese sensor si estuviese sano.
# MAGIC Positivo = el sensor empuja la RUL hacia abajo.
# MAGIC
# MAGIC Es una **sensibilidad local por oclusión**, no valores SHAP: las contribuciones no suman la
# MAGIC predicción, el modelo puede responder de forma poco fiable a combinaciones irreales (un sensor
# MAGIC sano con los demas degradados) y sensores correlacionados se reparten el efecto de forma
# MAGIC arbitraria. Sirve para priorizar que mirar, no para demostrar causalidad.

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

from lib.cmapss import (ACTIVE_SENSORS, HEALTHY_CYCLES, classify_state, feature_columns,  # noqa: E402
                        occlusion_contributions, phm08_score, recommended_action, rmse, top_k_sensors)

dbutils.widgets.text("catalog", "workspace", "Catálogo")
dbutils.widgets.text("schema", "mantenimiento", "Schema")
dbutils.widgets.text("umbral_critico", "20", "RUL <= umbral: critical")
dbutils.widgets.text("umbral_vigilar", "35", "RUL <= umbral: watch")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Nombre no valido: {_n!r}")
UMBRAL_CRITICO = float(dbutils.widgets.get("umbral_critico"))
UMBRAL_VIGILAR = float(dbutils.widgets.get("umbral_vigilar"))
assert 0 <= UMBRAL_CRITICO < UMBRAL_VIGILAR, "los umbrales deben cumplir 0 <= critico < vigilar"
FQ = f"`{CATALOG}`.`{SCHEMA}`"
MODEL_NAME = f"{CATALOG}.{SCHEMA}.rul_model"

# COMMAND ----------

import mlflow
from mlflow import MlflowClient

mlflow.set_registry_uri("databricks-uc")
version = MlflowClient().get_model_version_by_alias(MODEL_NAME, "champion")  # falla claro si no hay champion
modelo = mlflow.pyfunc.load_model(f"models:/{MODEL_NAME}@champion")
print("Champion:", MODEL_NAME, "version", version.version)

# COMMAND ----------

FEATS = feature_columns()
feat = spark.table(f"{FQ}.gold_features")
verdad = spark.table(f"{FQ}.gold_test_verdad").select("unit", "cycle_final", "rul_real")

test = (feat.filter("split = 'test'").select("unit", "cycle", *FEATS[1:])
        .join(verdad, (feat.unit == verdad.unit) & (feat.cycle == verdad.cycle_final))
        .select(feat.unit, feat.cycle, "rul_real", *FEATS[1:]).toPandas()
        .sort_values("unit").reset_index(drop=True))
assert len(test) == 100, f"se esperaban 100 motores y hay {len(test)}"
X = test[FEATS].astype("float64")

# Referencia sana: mediana de las variables en los primeros 30 ciclos de los motores de train.
sano = (feat.filter(f"split = 'train' AND cycle <= {HEALTHY_CYCLES}").select(*FEATS).toPandas()
        .astype("float64").median())

# COMMAND ----------

predict = lambda d: np.asarray(modelo.predict(d[FEATS]), dtype=float)
pred = np.clip(predict(X), 0.0, None)
contrib = occlusion_contributions(predict, X, sano, sensors=ACTIVE_SENSORS)
top = top_k_sensors(contrib, k=3)

out = pd.DataFrame({
    "unit": test["unit"].astype(int),
    "cycle": test["cycle"].astype(int),
    "rul_pred": pred.round(2),
    "rul_real": test["rul_real"].astype(int),
})
out["error"] = (out["rul_pred"] - out["rul_real"]).round(2)
out["estado"] = [classify_state(r, UMBRAL_CRITICO, UMBRAL_VIGILAR) for r in out["rul_pred"]]
for i in range(3):
    out[f"sensor_{i+1}"] = [t[i][0] for t in top]
    out[f"contrib_{i+1}"] = [round(t[i][1], 3) for t in top]
out["accion"] = [recommended_action(r.estado, r.rul_pred, [r.sensor_1, r.sensor_2]) for r in out.itertuples()]
out["umbral_critico"] = UMBRAL_CRITICO
out["umbral_vigilar"] = UMBRAL_VIGILAR
out["model_name"] = MODEL_NAME
out["model_version"] = int(version.version)

print(f"RMSE vs RUL real: {rmse(out.rul_real, out.rul_pred):.2f} | PHM08: {phm08_score(out.rul_real, out.rul_pred):.0f}")
print(out["estado"].value_counts().to_string())

# COMMAND ----------

from pyspark.sql import functions as F

sdf = spark.createDataFrame(out).withColumn("scored_at", F.current_timestamp())
(sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gold_predicciones"))
display(spark.table(f"{FQ}.gold_predicciones").orderBy("rul_pred").limit(15))
