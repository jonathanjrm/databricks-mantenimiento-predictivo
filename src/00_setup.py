# Databricks notebook source
# MAGIC %md
# MAGIC # 00. Preparacion del entorno
# MAGIC
# MAGIC Crea el schema y los volumes `raw` y `manuales`, y copia los ficheros de NASA C-MAPSS FD001
# MAGIC (que viajan en el repositorio, carpeta `data/cmapss/`) al volume `raw/cmapss/`.
# MAGIC
# MAGIC - Idempotente: se puede ejecutar varias veces; solo copia lo que falta o ha cambiado.
# MAGIC - No descarga nada de internet (Free Edition restringe la salida a dominios de confianza).
# MAGIC - Los PDF de los manuales **no** están en el repositorio: súbelos tu al volume `manuales`.

# COMMAND ----------

import os
import re
import shutil

dbutils.widgets.text("catalog", "workspace", "Catálogo")
dbutils.widgets.text("schema", "mantenimiento", "Schema")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()

# Los nombres se interpolan en SQL: se validan para evitar inyeccion y errores tontos.
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Nombre no valido: {_n!r} (letras, numeros y guion bajo)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Schema y volumes

# COMMAND ----------

# En Free Edition el catálogo `workspace` ya existe; no se crea ningún catálogo.
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}` COMMENT 'Mantenimiento predictivo de turbinas (demo)'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.`raw` COMMENT 'Ficheros C-MAPSS FD001 (entrada del pipeline)'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`.`manuales` COMMENT 'PDF de manuales para el RAG (subidos a mano)'")

RAW_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/raw/cmapss"
MANUALES_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/manuales"
os.makedirs(RAW_DIR, exist_ok=True)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Copiar los datos del repositorio al volume `raw`
# MAGIC
# MAGIC Los ficheros están en workspace files, en una ruta relativa a este notebook
# MAGIC (`../data/cmapss`). Se copian con `shutil` a la ruta FUSE del volume.

# COMMAND ----------

def repo_root() -> str:
    """Raiz del repositorio: carpeta padre de src/ (donde vive este notebook)."""
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        path = nb if nb.startswith("/Workspace") else "/Workspace" + nb
        return os.path.dirname(os.path.dirname(path))
    except Exception:  # verificar en docs: alternativa si no hay contexto de notebook
        return os.path.abspath(os.path.join(os.getcwd(), ".."))


SRC_DIR = os.path.join(repo_root(), "data", "cmapss")
FICHEROS = ["train_FD001.txt", "test_FD001.txt", "RUL_FD001.txt"]
# Lineas esperadas (comprobadas al descargar el dataset): 20631, 13096 y 100.
ESPERADO = {"train_FD001.txt": 20631, "test_FD001.txt": 13096, "RUL_FD001.txt": 100}

copiados, omitidos = [], []
for nombre in FICHEROS:
    origen = os.path.join(SRC_DIR, nombre)
    destino = os.path.join(RAW_DIR, nombre)
    if not os.path.exists(origen):
        raise FileNotFoundError(f"No encuentro {origen}. Despliega el repositorio completo (incluida data/cmapss).")
    if os.path.exists(destino) and os.path.getsize(destino) == os.path.getsize(origen):
        omitidos.append(nombre)
        continue
    shutil.copyfile(origen, destino)
    copiados.append(nombre)

print("Copiados :", copiados)
print("Ya estaban:", omitidos)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Comprobacion

# COMMAND ----------

for nombre, esperadas in ESPERADO.items():
    with open(os.path.join(RAW_DIR, nombre), "r", encoding="ascii") as fh:
        n = sum(1 for linea in fh if linea.strip())
    assert n == esperadas, f"{nombre}: {n} lineas (se esperaban {esperadas})"
    print(f"OK {nombre}: {n} lineas")

n_pdf = sum(1 for r, _, fs in os.walk(MANUALES_DIR) for f in fs if f.lower().endswith(".pdf"))
print(f"\nPDF en el volume manuales: {n_pdf}")
if n_pdf == 0:
    print(f"Sube tus PDF a {MANUALES_DIR} (Catalog > {CATALOG} > {SCHEMA} > manuales > Upload) antes de ejecutar 04_rag_index.")
