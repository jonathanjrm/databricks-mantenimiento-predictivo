# Databricks notebook source
# MAGIC %md
# MAGIC # 06. App permissions on the data
# MAGIC
# MAGIC The app (Databricks Apps) runs with its own service principal. This notebook grants it read
# MAGIC access to the project schema. Run it **after** deploying the app.
# MAGIC
# MAGIC - The SQL warehouse is granted as an app resource (`CAN_USE`), not here.
# MAGIC - AI Search: the service principal must be able to query the index. Check in your workspace
# MAGIC   the exact privilege on the endpoint and the index (Free Edition has no account console).
# MAGIC   If the assistant returns a permission error, this is the first thing to check.

# COMMAND ----------

import re

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "maintenance", "Schema")
dbutils.widgets.text("app_name", "predictive-maintenance-dev", "App name")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
APP = dbutils.widgets.get("app_name").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()
app = w.apps.get(name=APP)
sp = app.service_principal_client_id  # application ID of the app's service principal
if not sp or not re.fullmatch(r"[0-9a-fA-F\-]{20,}", sp):
    raise ValueError(f"Could not get the application ID of the service principal of app {APP!r}: {sp!r}")
print("App service principal:", sp)

# COMMAND ----------

# Least privilege: read only, on this catalog/schema. The principal is identified by its
# application ID (never by display name).
for statement in [
    f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `{sp}`",
    f"GRANT USE SCHEMA ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp}`",
    f"GRANT SELECT ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp}`",
]:
    spark.sql(statement)
    print("OK:", statement)

display(spark.sql(f"SHOW GRANTS `{sp}` ON SCHEMA `{CATALOG}`.`{SCHEMA}`"))
