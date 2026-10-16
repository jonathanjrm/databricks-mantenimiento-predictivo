"""BRONZE layer: incremental ingestion of the C-MAPSS files with Auto Loader.

Why streaming tables: the files land in the volume and are only ever appended. Auto Loader
remembers which files it has processed (checkpoint managed by the pipeline), so every run
reads only what is new. Bronze keeps the data as it arrives, plus lineage metadata (file
path, ingestion time) and the train/test split.
"""

import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F

# Makes the `lib` package importable (src/lib). The bundle passes the src/ path through the
# pipeline configuration (maintenance.src_root).
_SRC_ROOT = spark.conf.get("maintenance.src_root", "")
if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from lib.spark_schema import READINGS_SCHEMA

# Volume path holding the files; set by the bundle (resources/pipelines.yml).
RAW_PATH = spark.conf.get("maintenance.raw_path")


@dp.table(
    name="bronze_cmapss_readings",
    comment="Bronze: C-MAPSS FD001 sensor readings (train and test), as they arrive.",
    table_properties={"quality": "bronze"},
)
def bronze_cmapss_readings():
    # Explicit 26-column schema: the format is fixed and known, nothing to infer.
    # Every line ends with spaces; with sep=" " extra empty fields appear and are discarded
    # because the schema is shorter. rescuedDataColumn stays off because those empty fields
    # would fill _rescued_data on every row; type failures leave nulls that the silver
    # expectations detect.
    return (
        spark.readStream.format("cloudFiles")
        .option("cloudFiles.format", "csv")
        .option("sep", " ")
        .option("header", "false")
        .option("pathGlobFilter", "{train,test}_FD001.txt")  # excludes RUL_FD001.txt and the readme
        .schema(READINGS_SCHEMA)
        .load(RAW_PATH)
        .select(
            "*",
            F.when(F.col("_metadata.file_name").startswith("train"), F.lit("train"))
            .otherwise(F.lit("test"))
            .alias("split"),
            F.col("_metadata.file_path").alias("_source_file"),
            F.current_timestamp().alias("_ingest_ts"),
        )
    )


@dp.table(
    name="bronze_cmapss_rul",
    comment="Bronze: actual RUL of the test engines (the whole RUL_FD001.txt in one row).",
    table_properties={"quality": "bronze"},
)
def bronze_cmapss_rul():
    # wholetext: one row per file with the full content. The engine number is the position
    # of the value, so it does not depend on read order (see silver_rul_test).
    return (
        spark.readStream.format("cloudFiles")
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
