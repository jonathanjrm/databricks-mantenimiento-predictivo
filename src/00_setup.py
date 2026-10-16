# Databricks notebook source
# MAGIC %md
# MAGIC # 00. Environment setup
# MAGIC
# MAGIC Creates the schema and the volumes `raw`, `code` and `manuals`, and copies the NASA C-MAPSS FD001
# MAGIC files (shipped in the repository under `data/cmapss/`) to the volume `raw/cmapss/`.
# MAGIC
# MAGIC - Idempotent: it can run several times; it only copies what is missing or has changed.
# MAGIC - Nothing is downloaded from the internet (Free Edition restricts egress to trusted domains).
# MAGIC - The manual PDFs are **not** in the repository: upload them yourself to the `manuals` volume.

# COMMAND ----------

import os
import re
import shutil

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "maintenance", "Schema")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()

# The names are interpolated into SQL: validate them against injection and typos.
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r} (letters, digits and underscore only)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Schema and volumes

# COMMAND ----------

# In Free Edition the `workspace` catalog already exists; no catalog is created.
spark.sql(
    f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}` COMMENT 'Predictive turbine maintenance (demo)'"
)
spark.sql(
    f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.`raw` COMMENT 'C-MAPSS FD001 files (pipeline input)'"
)
spark.sql(
    f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.`code` "
    f"COMMENT 'Copy of the lib package (workaround for the workspace files read error)'"
)
spark.sql(
    f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.`manuals` "
    f"COMMENT 'Manual PDFs for the RAG (uploaded by hand)'"
)

RAW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/raw/cmapss"
MANUALS_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/manuals"
os.makedirs(RAW_DIR, exist_ok=True)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Copy the repository data to the `raw` volume
# MAGIC
# MAGIC The files live in workspace files, at a path relative to this notebook (`../data/cmapss`).
# MAGIC They are copied with `shutil` to the FUSE path of the volume.

# COMMAND ----------


def repo_root() -> str:
    """Repository root: parent folder of src/ (where this notebook lives)."""
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        path = nb if nb.startswith("/Workspace") else "/Workspace" + nb
        return os.path.dirname(os.path.dirname(path))
    except Exception:  # no notebook context (e.g. run as a plain script)
        return os.path.abspath(os.path.join(os.getcwd(), ".."))


SRC_DIR = os.path.join(repo_root(), "data", "cmapss")
FILES = ["train_FD001.txt", "test_FD001.txt", "RUL_FD001.txt"]
# Expected line counts (checked when the dataset was downloaded): 20631, 13096 and 100.
EXPECTED_LINES = {"train_FD001.txt": 20631, "test_FD001.txt": 13096, "RUL_FD001.txt": 100}

copied, skipped = [], []
for name in FILES:
    source = os.path.join(SRC_DIR, name)
    target = os.path.join(RAW_DIR, name)
    if os.path.exists(target) and name in EXPECTED_LINES:
        # Already uploaded (e.g. with `databricks fs cp`): the workspace is not read (see the I/O workaround).
        skipped.append(name)
        continue
    if not os.path.exists(source):
        raise FileNotFoundError(f"{source} not found. Deploy the full repository (including data/cmapss).")
    if os.path.exists(target) and os.path.getsize(target) == os.path.getsize(source):
        skipped.append(name)
        continue
    shutil.copyfile(source, target)
    copied.append(name)

print("Copied :", copied)
print("Already there:", skipped)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Check

# COMMAND ----------

for name, expected in EXPECTED_LINES.items():
    with open(os.path.join(RAW_DIR, name), encoding="ascii") as fh:
        n = sum(1 for line in fh if line.strip())
    assert n == expected, f"{name}: {n} lines (expected {expected})"
    print(f"OK {name}: {n} lines")

n_pdf = sum(1 for r, _, fs in os.walk(MANUALS_DIR) for f in fs if f.lower().endswith(".pdf"))
print(f"\nPDF files in the manuals volume: {n_pdf}")
if n_pdf == 0:
    print(
        f"Upload your PDFs to {MANUALS_DIR} (Catalog > {CATALOG} > {SCHEMA} > manuals > Upload) "
        "before running 04_rag_index."
    )
