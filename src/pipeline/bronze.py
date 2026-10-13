"""Capa BRONZE: ingesta incremental de los ficheros C-MAPSS con Auto Loader.

Por que streaming tables: los ficheros llegan al volume y solo se anaden (append-only).
Auto Loader recuerda que ficheros ya proceso (checkpoint gestionado por el pipeline), así
que cada ejecución lee unicamente lo nuevo. Bronze conserva el dato tal cual llega, mas
metadatos de linaje (ruta del fichero, momento de ingesta) y la particion train/test.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

import sys  # noqa: E402

# Hace importable el paquete `lib` (src/lib). El bundle pasa la ruta de src/ en la
# configuración del pipeline (mantenimiento.src_root). verificar en docs: sys.path y
# directorio de trabajo de los ficheros fuente de un pipeline.
_SRC_ROOT = spark.conf.get("mantenimiento.src_root", "")  # noqa: F821
if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from lib.spark_schema import READINGS_SCHEMA  # noqa: E402

# Ruta del volume con los ficheros; la fija el bundle (resources/pipelines.yml).
RAW_PATH = spark.conf.get("mantenimiento.raw_path")  # noqa: F821


@dp.table(
    name="bronze_cmapss_lecturas",
    comment="Bronze: lecturas de sensores C-MAPSS FD001 (train y test), tal cual llegan.",
    table_properties={"quality": "bronze"},
)
def bronze_cmapss_lecturas():
    # Esquema explicito (26 columnas): el formato es fijo y conocido, no hace falta inferir.
    # Los ficheros acaban cada linea con espacios; con sep=" " aparecen campos vacios extra
    # que se descartan porque el esquema es mas corto. No se activa rescuedDataColumn porque
    # esos campos vacios llenarian _rescued_data en todas las filas; los fallos de tipo
    # dejan nulos que la capa silver detecta con expectations.
    return (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "csv")
        .option("sep", " ")
        .option("header", "false")
        .option("pathGlobFilter", "{train,test}_FD001.txt")  # excluye RUL_FD001.txt y el readme
        .schema(READINGS_SCHEMA)
        .load(RAW_PATH)
        .select(
            "*",
            F.when(F.col("_metadata.file_name").startswith("train"), F.lit("train"))
             .otherwise(F.lit("test")).alias("split"),
            F.col("_metadata.file_path").alias("_source_file"),
            F.current_timestamp().alias("_ingest_ts"),
        )
    )


@dp.table(
    name="bronze_cmapss_rul",
    comment="Bronze: RUL real de los motores de test (RUL_FD001.txt completo en una fila).",
    table_properties={"quality": "bronze"},
)
def bronze_cmapss_rul():
    # wholetext: una fila por fichero con todo el contenido. El número de motor es la
    # posicion del valor, y así no depende del orden de lectura (ver silver_rul_test).
    return (
        spark.readStream.format("cloudFiles")  # noqa: F821
        .option("cloudFiles.format", "text")
        .option("wholetext", "true")
        .option("pathGlobFilter", "RUL_FD001.txt")
        .load(RAW_PATH)
        .select(
            "value",
            F.col("_metadata.file_path").alias("_source_file"),
            F.current_timestamp().alias("_ingest_ts"),
        )
    )
