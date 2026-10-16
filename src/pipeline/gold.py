"""GOLD layer: model features, test ground truth and health index.

Why materialized views: the rolling windows (means, standard deviations, slope) and the
reference statistics need the full history of every engine, which in streaming would
require unbounded state. A materialized view recomputes (or refreshes incrementally when
it can) from silver and is always consistent with it.

RUL label (train only): RUL_raw = last cycle of the engine - current cycle; RUL = min(RUL_raw, 125).
The clip at 125 cycles is standard practice in the C-MAPSS literature (Heimes, 2008; Li, Ding
and Sun, 2018): early in life there is no observable degradation and modelling the RUL as
piece-wise linear with a ceiling improves the estimate.
"""

import sys

from pyspark import pipelines as dp
from pyspark.sql import Window
from pyspark.sql import functions as F

# Makes the `lib` package importable (src/lib). The bundle passes the src/ path through the
# pipeline configuration (maintenance.src_root).
_SRC_ROOT = spark.conf.get("maintenance.src_root", "")
if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from lib import spark_features as SF
from lib.cmapss import RUL_CLIP


@dp.materialized_view(
    name="gold_features",
    comment=(
        f"Gold: rolling features per engine and cycle (train and test) and RUL label clipped "
        f"at {RUL_CLIP} (train only)."
    ),
    table_properties={"quality": "gold"},
    cluster_by=["split", "unit"],
)
@dp.expect_or_fail("key_present", "unit IS NOT NULL AND cycle IS NOT NULL")
@dp.expect_or_fail(
    "valid_train_rul", f"split <> 'train' OR (rul IS NOT NULL AND rul BETWEEN 0 AND {RUL_CLIP})"
)
def gold_features():
    silver = spark.read.table("silver_readings")
    return SF.add_rolling_features(SF.add_rul_train(silver))


@dp.materialized_view(
    name="gold_test_truth",
    comment=(
        "Gold: last observed cycle of every test engine joined with its actual RUL "
        "(RUL_FD001.txt). This is the ground truth the model is evaluated against."
    ),
    table_properties={"quality": "gold"},
)
@dp.expect_or_fail("truth_present", "rul_real IS NOT NULL")
def gold_test_truth():
    silver = spark.read.table("silver_readings").filter("split = 'test'")
    w = Window.partitionBy("unit").orderBy(F.col("cycle").desc())
    last = (
        silver.withColumn("_rn", F.row_number().over(w))
        .filter("_rn = 1")
        .select("unit", F.col("cycle").alias("last_cycle"))
    )
    rul = spark.read.table("silver_rul_test")
    return last.join(rul.select("unit", "rul_real"), "unit", "left")


@dp.materialized_view(
    name="gold_health_params",
    comment=(
        "Gold: health index parameters (1 row). Healthy reference = first 30 cycles of every "
        "train engine; weights = mean shift of every sensor over the last 30."
    ),
    table_properties={"quality": "gold"},
)
def gold_health_params():
    return SF.health_params(spark.read.table("silver_readings"))


@dp.materialized_view(
    name="gold_health_curve",
    comment=(
        "Gold: health index per engine and cycle (0 = healthy, 1 = mean level at failure). "
        "A summary view of the sensors, not a prediction."
    ),
    table_properties={"quality": "gold"},
    cluster_by=["split", "unit"],
)
def gold_health_curve():
    silver = spark.read.table("silver_readings")
    params = spark.read.table("gold_health_params")
    return SF.health_curve(silver, params)


@dp.materialized_view(
    name="gold_quality_control",
    comment="Gold: reconciliation of rows, engines and keys per split.",
    table_properties={"quality": "gold"},
)
@dp.expect_or_fail("unique_keys", "rows = distinct_keys")
@dp.expect("train_100_engines", "split <> 'train' OR engines = 100")
@dp.expect("test_100_engines", "split <> 'test' OR engines = 100")
def gold_quality_control():
    s = spark.read.table("silver_readings")
    return s.groupBy("split").agg(
        F.count(F.lit(1)).alias("rows"),
        F.countDistinct("unit", "cycle").alias("distinct_keys"),
        F.countDistinct("unit").alias("engines"),
        F.max("cycle").alias("max_cycle"),
    )
