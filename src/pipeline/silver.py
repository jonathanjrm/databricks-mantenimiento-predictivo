"""SILVER layer: typed, validated data without the constant columns.

Still a streaming table: the cleaning is row by row, with no aggregation, so it is
processed incrementally on whatever Bronze appends.

Columns dropped (no information in FD001, a single operating condition and a single fault
mode): setting3, s1, s5, s6, s10, s16, s18, s19.
  - setting3, s1, s5, s10, s16, s18, s19: standard deviation exactly 0 in train and test.
  - s6: only two values (21.60 and 21.61), quantisation noise.
Checked against the real data in tests/test_cmapss.py. setting1 and setting2 are kept
(they vary by noise) but are not model features.
"""

import sys

from pyspark import pipelines as dp

# Makes the `lib` package importable (src/lib). The bundle passes the src/ path through the
# pipeline configuration (maintenance.src_root).
_SRC_ROOT = spark.conf.get("maintenance.src_root", "")
if _SRC_ROOT and _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)
from lib.cmapss import CONSTANT_SENSORS, CONSTANT_SETTINGS
from lib.spark_features import parse_rul_wholetext
from lib.spark_schema import plausibility_rules

# Plausibility ranges: warning (expect), not drop. An out-of-range value usually means a
# faulty sensor or a unit error, which is useful information for operations, not
# something to delete silently.
RANGES = plausibility_rules()


@dp.table(
    name="silver_readings",
    comment="Silver: validated readings without the constant sensors. Key: (split, unit, cycle).",
    table_properties={"quality": "silver"},
    cluster_by=["unit", "cycle"],
)
@dp.expect_or_drop("valid_unit", "unit IS NOT NULL AND unit > 0")
@dp.expect_or_drop("valid_cycle", "cycle IS NOT NULL AND cycle > 0")
@dp.expect_or_drop("valid_split", "split IN ('train', 'test')")
@dp.expect_all(RANGES)
def silver_readings():
    return spark.readStream.table("bronze_cmapss_readings").drop(*CONSTANT_SENSORS, *CONSTANT_SETTINGS)


@dp.table(
    name="silver_rul_test",
    comment="Silver: actual RUL per test engine (unit = position in RUL_FD001.txt).",
    table_properties={"quality": "silver"},
)
@dp.expect_or_drop("valid_unit", "unit > 0")
@dp.expect_or_drop("valid_rul", "rul_real IS NOT NULL AND rul_real >= 0")
def silver_rul_test():
    return parse_rul_wholetext(spark.readStream.table("bronze_cmapss_rul"))
