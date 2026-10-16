"""Spark transformations (DataFrame -> DataFrame) implementing the `lib.cmapss` definitions
with native window functions. Used by the pipeline (gold) and testable with a local
SparkSession (tests/test_spark_equivalence.py).

They contain no pipeline decorators and no table reads: they take and return DataFrames,
as the Lakeflow documentation recommends for testability.
"""

from __future__ import annotations

from collections.abc import Iterable

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from lib.cmapss import ACTIVE_SENSORS, END_CYCLES, HEALTHY_CYCLES, RUL_CLIP, SLOPE_EPS, SLOPE_WINDOW, WINDOWS


def add_rul_train(df: DataFrame, clip: int = RUL_CLIP) -> DataFrame:
    """rul_raw and rul (clipped) for split='train' rows; null for test."""
    w = Window.partitionBy("split", "unit")
    raw = F.max("cycle").over(w) - F.col("cycle")
    is_train = F.col("split") == "train"
    return df.withColumn("rul_raw", F.when(is_train, raw).cast("int")).withColumn(
        "rul", F.when(is_train, F.least(raw, F.lit(clip))).cast("int")
    )


def add_rolling_features(df: DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS) -> DataFrame:
    """Backward rolling means and standard deviations (5 and 10) and slope (10), per engine."""
    base = Window.partitionBy("split", "unit").orderBy("cycle")
    cols = []
    cyc = F.col("cycle").cast("double")
    wslope = base.rowsBetween(-(SLOPE_WINDOW - 1), 0)
    n = F.count(F.lit(1)).over(wslope).cast("double")
    sx = F.sum(cyc).over(wslope)
    sxx = F.sum(cyc * cyc).over(wslope)
    den = n * sxx - sx * sx
    for s in sensors:
        for w in WINDOWS:
            win = base.rowsBetween(-(w - 1), 0)
            cols.append(F.avg(s).over(win).alias(f"{s}_mean{w}"))
            cols.append(F.coalesce(F.stddev_samp(s).over(win), F.lit(0.0)).alias(f"{s}_std{w}"))
        sy = F.sum(F.col(s)).over(wslope)
        sxy = F.sum(cyc * F.col(s)).over(wslope)
        slope = F.when(F.abs(den) > SLOPE_EPS, (n * sxy - sx * sy) / den).otherwise(F.lit(0.0))
        cols.append(slope.alias(f"{s}_slope{SLOPE_WINDOW}"))
    return df.select("*", *cols)


def health_params(silver: DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS) -> DataFrame:
    """Health index parameters (1 row, wide format), fitted on train only.

    Columns: mu_<s>, sd_<s>, w_<s> for every sensor, plus `norm`. Same definition as
    lib.cmapss.fit_health_params (pandas reference).
    """
    sensors = list(sensors)
    tr = silver.filter(F.col("split") == "train")
    healthy = tr.filter(F.col("cycle") <= HEALTHY_CYCLES)
    stats = healthy.agg(
        *[x for s in sensors for x in (F.avg(s).alias(f"mu_{s}"), F.stddev_samp(s).alias(f"sd_{s}"))]
    )
    stats = stats.select(
        *[F.col(f"mu_{s}") for s in sensors],
        *[
            F.when(F.col(f"sd_{s}") > 0, F.col(f"sd_{s}")).otherwise(F.lit(1.0)).alias(f"sd_{s}")
            for s in sensors
        ],
    )
    last_c = F.max("cycle").over(Window.partitionBy("unit"))
    end = tr.withColumn("_max", last_c).filter(F.col("cycle") > F.col("_max") - END_CYCLES)
    ez = end.crossJoin(stats)
    w_row = ez.agg(
        *[F.avg((F.col(s) - F.col(f"mu_{s}")) / F.col(f"sd_{s}")).alias(f"w_{s}") for s in sensors]
    )
    p = stats.crossJoin(w_row)
    den = sum(F.abs(F.col(f"w_{s}")) for s in sensors)
    raw = sum(F.col(f"w_{s}") * (F.col(s) - F.col(f"mu_{s}")) / F.col(f"sd_{s}") for s in sensors) / den
    norm = end.crossJoin(p).agg(F.avg(raw).alias("norm"))
    return p.crossJoin(norm)


def health_curve(
    silver: DataFrame, params: DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS, smooth: int = 5
) -> DataFrame:
    """Smoothed health index per engine and cycle (0 healthy, 1 mean level at failure)."""
    sensors = list(sensors)
    j = silver.crossJoin(params)
    den = sum(F.abs(F.col(f"w_{s}")) for s in sensors)
    raw = sum(F.col(f"w_{s}") * (F.col(s) - F.col(f"mu_{s}")) / F.col(f"sd_{s}") for s in sensors) / den
    j = j.withColumn("_hi", raw / F.col("norm"))
    win = Window.partitionBy("split", "unit").orderBy("cycle").rowsBetween(-(smooth - 1), 0)
    return j.withColumn("health_index", F.avg("_hi").over(win)).select(
        "split", "unit", "cycle", "health_index"
    )


def parse_rul_wholetext(df: DataFrame, value_col: str = "value") -> DataFrame:
    """RUL_FD001.txt read with wholetext (one row holding the whole file) -> (unit, rul_real).

    The position of a value in the file is the engine number (1..100): reading the file
    whole avoids depending on row order in a distributed read.
    """
    clean = F.regexp_replace(F.col(value_col), r"^\s+|\s+$", "")
    keep = [c for c in df.columns if c != value_col]
    return df.select(*keep, F.posexplode(F.split(clean, r"\s+")).alias("_pos", "_tok")).select(
        *keep,
        (F.col("_pos") + 1).cast("int").alias("unit"),
        F.col("_tok").cast("int").alias("rul_real"),
    )
