# Databricks notebook source
# MAGIC %md
# MAGIC # 05. Evaluacion del retrieval del RAG
# MAGIC
# MAGIC Mide si el índice recupera el fragmento correcto. El conjunto de evaluación **se genera a partir
# MAGIC de los propios fragmentos** (no se escribe a mano):
# MAGIC
# MAGIC 1. Se muestrean N fragmentos (semilla fija).
# MAGIC 2. Un LLM de Foundation Model APIs redacta una pregunta que ese fragmento responde. Si el LLM no
# MAGIC    esta disponible, se usa un respaldo extractivo (terminos mas raros de la frase mas informativa).
# MAGIC 3. Se consulta el índice con la pregunta y se comprueba en que posicion aparece el fragmento de origen.
# MAGIC
# MAGIC Metricas: **recall@k** (el fragmento esta entre los k primeros) y **MRR** (media del inverso de
# MAGIC la posicion). Se registran en MLflow junto con la tabla de preguntas.
# MAGIC
# MAGIC Limites honestos: (a) la relevancia es estricta (solo cuenta el fragmento de origen; un fragmento
# MAGIC vecino por el solape también responderia), así que las cifras son un suelo; (b) las preguntas
# MAGIC extractivas comparten vocabulario con el fragmento y favorecen la busqueda lexica; (c) mide
# MAGIC retrieval, no la calidad de la respuesta generada.

# COMMAND ----------

# MAGIC %pip install -q databricks-ai-search
# MAGIC

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import re
import sys

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
from lib.retrieval import doc_frequency, evaluate_retrieval, extractive_question  # noqa: E402

dbutils.widgets.text("catalog", "workspace", "Catálogo")
dbutils.widgets.text("schema", "mantenimiento", "Schema")
dbutils.widgets.text("endpoint_name", "mantenimiento_ai_search", "Endpoint de AI Search")
dbutils.widgets.text("n_preguntas", "40", "Numero de preguntas")
dbutils.widgets.dropdown("usar_llm", "si", ["si", "no"], "Generar preguntas con LLM")
# verificar disponibilidad en Free Edition: nombre del endpoint de chat de Foundation Model APIs
dbutils.widgets.text("llm_endpoint", "databricks-gpt-oss-120b", "Endpoint de chat")
dbutils.widgets.dropdown("query_type", "HYBRID", ["ANN", "HYBRID"], "Tipo de busqueda")
CATALOG = dbutils.widgets.get("catalog").strip()
SCHEMA = dbutils.widgets.get("schema").strip()
for _n in (CATALOG, SCHEMA):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", _n):
        raise ValueError(f"Nombre no valido: {_n!r}")
ENDPOINT = dbutils.widgets.get("endpoint_name").strip()
N = int(dbutils.widgets.get("n_preguntas"))
USAR_LLM = dbutils.widgets.get("usar_llm") == "si"
LLM = dbutils.widgets.get("llm_endpoint").strip()
QTYPE = dbutils.widgets.get("query_type")
FQ = f"`{CATALOG}`.`{SCHEMA}`"
INDEX = f"{CATALOG}.{SCHEMA}.manual_chunks_index"
KS = (1, 3, 5)

# COMMAND ----------

chunks = spark.table(f"{FQ}.manual_chunks").select("chunk_id", "source", "page", "text").toPandas()
chunks = chunks[chunks["text"].str.split().str.len() >= 40].reset_index(drop=True)  # fragmentos con contenido
muestra = chunks.sample(n=min(N, len(chunks)), random_state=42).reset_index(drop=True)
print(f"{len(chunks)} fragmentos utiles; evaluo {len(muestra)}")

# COMMAND ----------

from mlflow.deployments import get_deploy_client

PROMPT = (
    "Lee el fragmento de un manual técnico y escribe UNA pregunta que un técnico de mantenimiento haria "
    "y que ese fragmento responde. Reglas: no copies frases literales, no menciones 'el fragmento' ni "
    "'el texto', una sola linea, en el mismo idioma que el fragmento. Responde solo con la pregunta.\n\n"
    "Fragmento:\n{texto}"
)


def texto_de_respuesta(resp) -> str:
    """Extrae el texto de una respuesta de chat; algunos modelos devuelven una lista de bloques."""
    msg = resp["choices"][0]["message"]["content"]
    if isinstance(msg, list):
        msg = " ".join(b.get("text", "") for b in msg if isinstance(b, dict) and b.get("type") in (None, "text"))
    return str(msg).strip().strip('"').splitlines()[0].strip() if str(msg).strip() else ""


def pregunta_llm(texto: str):
    try:
        cli = get_deploy_client("databricks")
        r = cli.predict(endpoint=LLM, inputs={
            "messages": [{"role": "user", "content": PROMPT.format(texto=texto[:3000])}],
            "max_tokens": 400, "temperature": 0.2})
        q = texto_de_respuesta(r)
        return q if len(q.split()) >= 4 else None
    except Exception as e:
        print("LLM no disponible o error:", type(e).__name__, str(e)[:120])
        return "ERROR"


df_freq = doc_frequency(chunks["text"].tolist())
preguntas, origen = [], []
llm_ok = USAR_LLM
for t in muestra["text"]:
    q = None
    if llm_ok:
        q = pregunta_llm(t)
        if q == "ERROR":      # un fallo desactiva el LLM para el resto (evita N errores iguales)
            llm_ok, q = False, None
    if q:
        preguntas.append(q); origen.append("llm")
    else:
        preguntas.append(extractive_question(t, df_freq, len(chunks))); origen.append("extractiva")
muestra["pregunta"], muestra["origen_pregunta"] = preguntas, origen
print(muestra["origen_pregunta"].value_counts().to_string())
display(muestra[["source", "page", "pregunta", "origen_pregunta"]].head(10))

# COMMAND ----------

from databricks.ai_search.client import AISearchClient

index = AISearchClient().get_index(endpoint_name=ENDPOINT, index_name=INDEX)
rankings = []
for q in muestra["pregunta"]:
    r = index.similarity_search(query_text=q, columns=["chunk_id"], num_results=max(KS), query_type=QTYPE)
    rankings.append([fila[0] for fila in r["result"]["data_array"]])
muestra["recuperados"] = [",".join(r) for r in rankings]
métricas = evaluate_retrieval(rankings, [{cid} for cid in muestra["chunk_id"]], ks=KS)
print({k: round(v, 3) if isinstance(v, float) else v for k, v in métricas.items()})

# COMMAND ----------

import mlflow

usuario = spark.sql("SELECT current_user()").first()[0]
mlflow.set_experiment(f"/Users/{usuario}/mantenimiento_rag_eval")
with mlflow.start_run(run_name=f"retrieval_{QTYPE.lower()}"):
    mlflow.log_params({"index": INDEX, "query_type": QTYPE, "n_preguntas": len(muestra),
                       "generador": "llm:" + LLM if llm_ok else "extractiva",
                       "n_chunks_indice": len(chunks), "k_max": max(KS)})
    mlflow.log_metrics({k: float(v) for k, v in métricas.items()})
    mlflow.log_table(muestra[["chunk_id", "source", "page", "pregunta", "origen_pregunta", "recuperados"]],
                     artifact_file="eval_retrieval.json")

# Tabla persistente para consultarla desde SQL o desde un dashboard.
(spark.createDataFrame(muestra[["chunk_id", "source", "page", "pregunta", "origen_pregunta", "recuperados"]])
      .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.rag_eval_preguntas"))
