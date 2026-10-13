# Databricks notebook source
# MAGIC %md
# MAGIC # 06. Permisos de la app sobre los datos
# MAGIC
# MAGIC La app (Databricks Apps) se ejecuta con su propio service principal. Este notebook le concede
# MAGIC lectura sobre el schema del proyecto. Ejecutalo **después** de desplegar la app.
# MAGIC
# MAGIC - El warehouse SQL ya se concede como recurso de la app (`CAN_USE`), no aquí.
# MAGIC - AI Search: el service principal necesita poder consultar el índice. verificar en docs el
# MAGIC   privilegio exacto sobre el endpoint y el índice en tu workspace (en Free Edition no hay consola
# MAGIC   de cuenta). Si la pregunta del asistente devuelve un error de permisos, es lo primero a revisar.

# COMMAND ----------

import re

dbutils.widgets.text("catalog", "workspace", "Catálogo")
dbutils.widgets.text("schema", "mantenimiento", "Schema")
dbutils.widgets.text("app_name", "mantenimiento-predictivo", "Nombre de la app")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
APP = dbutils.widgets.get("app_name").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Nombre no valido: {_n!r}")

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
app = w.apps.get(name=APP)
sp = app.service_principal_client_id  # application ID del service principal de la app
if not sp or not re.fullmatch(r"[0-9a-fA-F\-]{20,}", sp):
    raise ValueError(f"No obtengo el application ID del service principal de la app {APP!r}: {sp!r}")
print("Service principal de la app:", sp)

# COMMAND ----------

# Minimo privilegio: solo lectura, sobre este catálogo/schema. El principal se identifica por
# su application ID (nunca por nombre visible).
for sentencia in [
    f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `{sp}`",
    f"GRANT USE SCHEMA ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp}`",
    f"GRANT SELECT ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp}`",
]:
    spark.sql(sentencia)
    print("OK:", sentencia)

display(spark.sql(f"SHOW GRANTS `{sp}` ON SCHEMA `{CATALOG}`.`{SCHEMA}`"))
