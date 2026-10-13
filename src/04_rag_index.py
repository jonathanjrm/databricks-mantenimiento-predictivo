# Databricks notebook source
# MAGIC %md
# MAGIC # 04. Índice de manuales para el RAG (AI Search)
# MAGIC
# MAGIC 1. Lee todos los PDF de `/Volumes/<catálogo>/<schema>/manuales/` (cualquier PDF que haya).
# MAGIC 2. Extrae el texto página a página con `pypdf`. Si un PDF no tiene capa de texto (escaneado)
# MAGIC    o `parser = ai_parse_document`, usa la función SQL `ai_parse_document`.
# MAGIC 3. Trocea (~800 tokens con solape de ~100) **dentro de cada página**, para poder citar archivo y página.
# MAGIC 4. Guarda la tabla Delta `manual_chunks` con Change Data Feed activado.
# MAGIC 5. Crea (si no existe) el endpoint de AI Search y un índice Delta Sync con **embeddings gestionados**.
# MAGIC
# MAGIC Free Edition: un solo endpoint de AI Search con una unidad. Se reutiliza si ya existe.

# COMMAND ----------

# MAGIC %pip install -q pypdf databricks-ai-search

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import json
import os
import re
import sys

def _src_dir():
    try:
        nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        return os.path.dirname(nb if nb.startswith("/Workspace") else "/Workspace" + nb)
    except Exception:  # verificar en docs
        return os.getcwd()

sys.path.insert(0, _src_dir())
from lib.chunking import chunk_pages  # noqa: E402

dbutils.widgets.text("catalog", "workspace", "Catálogo")
dbutils.widgets.text("schema", "mantenimiento", "Schema")
dbutils.widgets.dropdown("parser", "auto", ["auto", "pypdf", "ai_parse_document"], "Extraccion de texto")
dbutils.widgets.text("endpoint_name", "mantenimiento_ai_search", "Endpoint de AI Search")
# Modelo de embeddings gestionado por Databricks. verificar en docs / pestana Serving del workspace:
# la lista cambia (alternativas citadas en la doc de oct 2026: databricks-bge-large-en,
# databricks-qwen3-embedding-0-6b en Public Preview, este último multilingue). gte-large-en es solo inglés.
dbutils.widgets.text("embedding_model", "databricks-gte-large-en", "Modelo de embeddings")
dbutils.widgets.text("chunk_tokens", "800", "Tokens por fragmento (aprox.)")
dbutils.widgets.text("overlap_tokens", "100", "Solape (aprox.)")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Nombre no valido: {_n!r}")
PARSER = dbutils.widgets.get("parser")
ENDPOINT = dbutils.widgets.get("endpoint_name").strip()
EMB_MODEL = dbutils.widgets.get("embedding_model").strip()
MAX_TOK = int(dbutils.widgets.get("chunk_tokens"))
OVERLAP = int(dbutils.widgets.get("overlap_tokens"))
FQ = f"`{CATALOG}`.`{SCHEMA}`"
TABLE = f"{CATALOG}.{SCHEMA}.manual_chunks"
INDEX = f"{CATALOG}.{SCHEMA}.manual_chunks_index"
MANUALES_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/manuales"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Localizar los PDF

# COMMAND ----------

pdfs = sorted(os.path.join(r, f) for r, _, fs in os.walk(MANUALES_DIR) for f in fs if f.lower().endswith(".pdf"))
if not pdfs:
    raise FileNotFoundError(
        f"No hay PDF en {MANUALES_DIR}. Sube los manuales al volume `manuales` "
        f"(Catalog > {CATALOG} > {SCHEMA} > manuales > Upload) y vuelve a ejecutar.")
print(f"{len(pdfs)} PDF:")
for p in pdfs:
    print(" -", os.path.relpath(p, MANUALES_DIR))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Extraer texto por página

# COMMAND ----------

def pages_pypdf(path):
    from pypdf import PdfReader
    reader = PdfReader(path)
    return [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]


def pages_ai_parse(path):
    """Fallback con la función SQL ai_parse_document (lee el PDF binario en el warehouse/serverless).

    verificar en docs: la estructura exacta del resultado (variant). Se asume
    document.elements[] con `content` y `bbox[0].page_id`; si cambia, ajusta aquí.
    Disponibilidad en Free Edition: verificar en docs.
    """
    escaped = path.replace("'", "''")
    fila = spark.sql(
        f"SELECT to_json(ai_parse_document(content)) AS j FROM read_files('{escaped}', format => 'binaryFile')"
    ).first()
    doc = json.loads(fila["j"])
    elementos = (doc.get("document") or {}).get("elements") or []
    por_pagina = {}
    for el in elementos:
        bbox = el.get("bbox") or [{}]
        page_id = int(bbox[0].get("page_id", 0)) + 1  # verificar en docs: ¿base 0?
        por_pagina.setdefault(page_id, []).append(el.get("content") or "")
    return [(p, "\n".join(t)) for p, t in sorted(por_pagina.items())]


def extraer(path):
    if PARSER == "ai_parse_document":
        return pages_ai_parse(path), "ai_parse_document"
    try:
        pages = pages_pypdf(path)
        chars = sum(len(t) for _, t in pages)
        if PARSER == "auto" and pages and chars / len(pages) < 80:  # casi sin texto: probablemente escaneado
            print(f"   poco texto con pypdf ({chars} caracteres en {len(pages)} páginas): pruebo ai_parse_document")
            return pages_ai_parse(path), "ai_parse_document"
        return pages, "pypdf"
    except Exception as e:  # PDF cifrado o danado
        if PARSER == "pypdf":
            raise
        print(f"   pypdf fallo ({type(e).__name__}): pruebo ai_parse_document")
        return pages_ai_parse(path), "ai_parse_document"


filas = []
for ruta in pdfs:
    nombre = os.path.relpath(ruta, MANUALES_DIR)
    pages, metodo = extraer(ruta)
    trozos = chunk_pages(nombre, pages, MAX_TOK, OVERLAP)
    for t in trozos:
        t["parser"] = metodo
    filas.extend(trozos)
    print(f"{nombre}: {len(pages)} páginas, {len(trozos)} fragmentos ({metodo})")

if not filas:
    raise ValueError("No se obtuvo texto de ningún PDF (páginas vacias o escaneadas sin OCR disponible).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Tabla Delta con Change Data Feed
# MAGIC El índice Delta Sync necesita CDF en la tabla origen y una clave primaria (`chunk_id`, estable:
# MAGIC hash de archivo, página e índice).

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

esquema = StructType([
    StructField("chunk_id", StringType(), False), StructField("source", StringType()),
    StructField("page", IntegerType()), StructField("chunk_idx", IntegerType()),
    StructField("text", StringType()), StructField("n_tokens_est", IntegerType()),
    StructField("parser", StringType()),
])
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {FQ}.manual_chunks (
  chunk_id STRING NOT NULL, source STRING, page INT, chunk_idx INT,
  text STRING, n_tokens_est INT, parser STRING, ingested_at TIMESTAMP
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
COMMENT 'Fragmentos de los manuales (RAG). Fuente del índice de AI Search.'
""")
spark.sql(f"ALTER TABLE {FQ}.manual_chunks SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
df = spark.createDataFrame([(r["chunk_id"], r["source"], r["page"], r["chunk_idx"], r["text"],
                             r["n_tokens_est"], r["parser"]) for r in filas], esquema) \
          .withColumn("ingested_at", F.current_timestamp())
# Sobrescritura completa: refleja PDF borrados o cambiados. El CDF registra los cambios y el
# índice sincroniza solo las diferencias.
df.write.mode("overwrite").saveAsTable(f"{FQ}.manual_chunks")
print("Fragmentos en la tabla:", spark.table(f"{FQ}.manual_chunks").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Endpoint e índice de AI Search (Delta Sync, embeddings gestionados)

# COMMAND ----------

from databricks.ai_search.client import AISearchClient

client = AISearchClient()  # dentro de un notebook toma las credenciales del contexto

existentes = [e.get("name") for e in (client.list_endpoints() or {}).get("endpoints", [])]
if ENDPOINT not in existentes:
    print("Creando endpoint", ENDPOINT, "(puede tardar varios minutos)")
    client.create_endpoint_and_wait(name=ENDPOINT, endpoint_type="STANDARD")
else:
    print("Endpoint ya existe:", ENDPOINT)

indices = [i.get("name") for i in (client.list_indexes(name=ENDPOINT) or {}).get("vector_indexes", [])]
if INDEX not in indices:
    print("Creando índice", INDEX)
    index = client.create_delta_sync_index_and_wait(
        endpoint_name=ENDPOINT,
        index_name=INDEX,
        source_table_name=TABLE,
        pipeline_type="TRIGGERED",             # sincroniza al pedirlo; en Free Edition evita consumo continuo
        primary_key="chunk_id",
        embedding_source_column="text",
        embedding_model_endpoint_name=EMB_MODEL,
        columns_to_sync=["source", "page", "chunk_idx", "text"],  # la clave primaria siempre se sincroniza
    )
else:
    print("Índice ya existe: sincronizo cambios")
    index = client.get_index(endpoint_name=ENDPOINT, index_name=INDEX)
    index.sync()
index.wait_until_ready()
print(index.describe().get("status", {}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Prueba rápida

# COMMAND ----------

res = index.similarity_search(
    query_text="mantenimiento preventivo", columns=["chunk_id", "source", "page", "text"],
    num_results=3, query_type="HYBRID")
for fila in res["result"]["data_array"]:
    print(f"{fila[1]} p.{fila[2]}  score={fila[-1]:.3f}\n   {fila[3][:160]!r}")
