# Databricks notebook source
# MAGIC %md
# MAGIC # 04. Manuals index for the RAG (AI Search)
# MAGIC
# MAGIC 1. Reads every PDF under `/Volumes/<catalog>/<schema>/manuals/` (any PDF found there).
# MAGIC 2. Extracts the text page by page with `pypdf`. If a PDF has no text layer (scanned)
# MAGIC    or `parser = ai_parse_document`, the SQL function `ai_parse_document` is used instead.
# MAGIC 3. Chunks (~800 tokens with ~100 overlap) **within each page**, so that file and page can be cited.
# MAGIC 4. Saves the Delta table `manual_chunks` with Change Data Feed enabled.
# MAGIC 5. Creates (if missing) the AI Search endpoint and a Delta Sync index with **managed embeddings**.
# MAGIC
# MAGIC Free Edition: a single AI Search endpoint with one unit. It is reused if it already exists.

# COMMAND ----------

# MAGIC %pip install -q pypdf databricks-ai-search

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import json
import os
import re
import sys


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
from lib.chunking import chunk_pages

dbutils.widgets.text("catalog", "workspace", "Catalog")
dbutils.widgets.text("schema", "maintenance", "Schema")
dbutils.widgets.dropdown("parser", "auto", ["auto", "pypdf", "ai_parse_document"], "Text extraction")
# Free Edition allows a single AI Search endpoint; it is created once and reused afterwards.
dbutils.widgets.text("endpoint_name", "maintenance_ai_search", "AI Search endpoint")
# Databricks-managed embedding model. The list changes over time (check the Serving tab):
# alternatives in the October 2026 docs are databricks-bge-large-en and
# databricks-qwen3-embedding-0-6b (Public Preview, multilingual). gte-large-en is English only.
dbutils.widgets.text("embedding_model", "databricks-gte-large-en", "Embedding model")
dbutils.widgets.text("chunk_tokens", "800", "Tokens per chunk (approx.)")
dbutils.widgets.text("overlap_tokens", "100", "Overlap (approx.)")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Invalid name: {_n!r}")
PARSER = dbutils.widgets.get("parser")
ENDPOINT = dbutils.widgets.get("endpoint_name").strip()
EMB_MODEL = dbutils.widgets.get("embedding_model").strip()
MAX_TOK = int(dbutils.widgets.get("chunk_tokens"))
OVERLAP = int(dbutils.widgets.get("overlap_tokens"))
FQ = f"`{CATALOG}`.`{SCHEMA}`"
TABLE = f"{CATALOG}.{SCHEMA}.manual_chunks"
INDEX = f"{CATALOG}.{SCHEMA}.manual_chunks_index"
MANUALS_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/manuals"
MIN_CHARS_PER_PAGE = 80  # below this average, pypdf found almost no text: probably a scanned PDF

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Locate the PDFs

# COMMAND ----------

pdfs = sorted(
    os.path.join(r, f) for r, _, fs in os.walk(MANUALS_DIR) for f in fs if f.lower().endswith(".pdf")
)
if not pdfs:
    raise FileNotFoundError(
        f"No PDF under {MANUALS_DIR}. Upload the manuals to the `manuals` volume "
        f"(Catalog > {CATALOG} > {SCHEMA} > manuals > Upload) and run again."
    )
print(f"{len(pdfs)} PDF:")
for p in pdfs:
    print(" -", os.path.relpath(p, MANUALS_DIR))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Extract text per page

# COMMAND ----------


def pages_pypdf(path: str) -> list[tuple[int, str]]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    return [(i + 1, (page.extract_text() or "")) for i, page in enumerate(reader.pages)]


def pages_ai_parse(path: str) -> list[tuple[int, str]]:
    """Fallback with the SQL function ai_parse_document (reads the binary PDF on serverless).

    The result structure (variant) is assumed to be document.elements[] with `content` and
    `bbox[0].page_id` (0-based); adjust here if the function output changes.
    """
    escaped = path.replace("'", "''")
    row = spark.sql(
        f"SELECT to_json(ai_parse_document(content)) AS j "
        f"FROM read_files('{escaped}', format => 'binaryFile')"
    ).first()
    doc = json.loads(row["j"])
    elements = (doc.get("document") or {}).get("elements") or []
    by_page: dict[int, list[str]] = {}
    for el in elements:
        bbox = el.get("bbox") or [{}]
        page_id = int(bbox[0].get("page_id", 0)) + 1
        by_page.setdefault(page_id, []).append(el.get("content") or "")
    return [(p, "\n".join(t)) for p, t in sorted(by_page.items())]


def extract(path: str) -> tuple[list[tuple[int, str]], str]:
    if PARSER == "ai_parse_document":
        return pages_ai_parse(path), "ai_parse_document"
    try:
        pages = pages_pypdf(path)
        chars = sum(len(t) for _, t in pages)
        if PARSER == "auto" and pages and chars / len(pages) < MIN_CHARS_PER_PAGE:
            print(
                f"   little text with pypdf ({chars} characters in {len(pages)} pages): "
                "trying ai_parse_document"
            )
            return pages_ai_parse(path), "ai_parse_document"
        return pages, "pypdf"
    except Exception as e:  # encrypted or damaged PDF
        if PARSER == "pypdf":
            raise
        print(f"   pypdf failed ({type(e).__name__}): trying ai_parse_document")
        return pages_ai_parse(path), "ai_parse_document"


rows = []
for path in pdfs:
    name = os.path.relpath(path, MANUALS_DIR)
    pages, method = extract(path)
    chunks = chunk_pages(name, pages, MAX_TOK, OVERLAP)
    for c in chunks:
        c["parser"] = method
    rows.extend(chunks)
    print(f"{name}: {len(pages)} pages, {len(chunks)} chunks ({method})")

if not rows:
    raise ValueError("No text obtained from any PDF (empty pages or scanned without OCR available).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Delta table with Change Data Feed
# MAGIC The Delta Sync index needs CDF on the source table and a primary key (`chunk_id`, stable:
# MAGIC hash of file, page and index).

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, StructField, StructType

chunks_schema = StructType(
    [
        StructField("chunk_id", StringType(), False),
        StructField("source", StringType()),
        StructField("page", IntegerType()),
        StructField("chunk_idx", IntegerType()),
        StructField("text", StringType()),
        StructField("n_tokens_est", IntegerType()),
        StructField("parser", StringType()),
    ]
)
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {FQ}.manual_chunks (
  chunk_id STRING NOT NULL, source STRING, page INT, chunk_idx INT,
  text STRING, n_tokens_est INT, parser STRING, ingested_at TIMESTAMP
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
COMMENT 'Manual chunks (RAG). Source of the AI Search index.'
""")
spark.sql(f"ALTER TABLE {FQ}.manual_chunks SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
df = spark.createDataFrame(
    [
        (r["chunk_id"], r["source"], r["page"], r["chunk_idx"], r["text"], r["n_tokens_est"], r["parser"])
        for r in rows
    ],
    chunks_schema,
).withColumn("ingested_at", F.current_timestamp())
# Full overwrite: reflects deleted or changed PDFs. The CDF records the changes and the
# index syncs only the differences.
df.write.mode("overwrite").saveAsTable(f"{FQ}.manual_chunks")
print("Chunks in the table:", spark.table(f"{FQ}.manual_chunks").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. AI Search endpoint and index (Delta Sync, managed embeddings)

# COMMAND ----------

from databricks.ai_search.client import AISearchClient

client = AISearchClient()  # inside a notebook it takes the credentials from the context

existing = [e.get("name") for e in (client.list_endpoints() or {}).get("endpoints", [])]
if ENDPOINT not in existing:
    print("Creating endpoint", ENDPOINT, "(this can take several minutes)")
    client.create_endpoint_and_wait(name=ENDPOINT, endpoint_type="STANDARD")
else:
    print("Endpoint already exists:", ENDPOINT)

indexes = [i.get("name") for i in (client.list_indexes(name=ENDPOINT) or {}).get("vector_indexes", [])]
if INDEX not in indexes:
    print("Creating index", INDEX)
    index = client.create_delta_sync_index_and_wait(
        endpoint_name=ENDPOINT,
        index_name=INDEX,
        source_table_name=TABLE,
        pipeline_type="TRIGGERED",  # syncs on request; avoids continuous consumption in Free Edition
        primary_key="chunk_id",
        embedding_source_column="text",
        embedding_model_endpoint_name=EMB_MODEL,
        columns_to_sync=["source", "page", "chunk_idx", "text"],  # the primary key is always synced
    )
else:
    print("Index already exists: syncing changes")
    index = client.get_index(endpoint_name=ENDPOINT, index_name=INDEX)
    index.sync()
index.wait_until_ready()
print(index.describe().get("status", {}))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Quick check

# COMMAND ----------

res = index.similarity_search(
    query_text="preventive maintenance",
    columns=["chunk_id", "source", "page", "text"],
    num_results=3,
    query_type="HYBRID",
)
for row in res["result"]["data_array"]:
    print(f"{row[1]} p.{row[2]}  score={row[-1]:.3f}\n   {row[3][:160]!r}")
