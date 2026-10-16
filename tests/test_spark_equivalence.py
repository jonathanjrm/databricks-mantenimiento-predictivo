"""Checks that the Spark window functions give the same results as the pandas reference.

Skipped without pyspark or Java. Uses 6 real FD001 engines.
"""

import os
import shutil

import numpy as np
import pytest

pyspark = pytest.importorskip("pyspark")
if shutil.which("java") is None:
    pytest.skip("Java not available", allow_module_level=True)

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from lib import cmapss as C
from lib import spark_features as SF
from lib.spark_schema import READINGS_SCHEMA, plausibility_rules

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "cmapss")


@pytest.fixture(scope="module")
def spark():
    s = (
        SparkSession.builder.master("local[2]")
        .appName("tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.driver.bindAddress", "127.0.0.1")
        .config("spark.driver.host", "127.0.0.1")
        .config("spark.driver.memory", "3g")
        .config("spark.sql.execution.arrow.pyspark.enabled", "false")
        .getOrCreate()
    )
    yield s
    s.stop()


def test_csv_read_with_explicit_schema(spark):
    df = (
        spark.read.format("csv")
        .option("sep", " ")
        .option("header", "false")
        .schema(READINGS_SCHEMA)
        .load(os.path.join(DATA, "train_FD001.txt"))
    )
    assert df.count() == 20631 and len(df.columns) == 26
    assert df.filter(F.col("unit").isNull() | F.col("s21").isNull()).count() == 0
    assert df.select("unit").distinct().count() == 100


def test_rul_wholetext(spark):
    df = spark.read.format("text").option("wholetext", "true").load(os.path.join(DATA, "RUL_FD001.txt"))
    assert df.count() == 1
    ex = SF.parse_rul_wholetext(df)
    pdf = ex.orderBy("unit").toPandas()
    ref = C.read_rul(os.path.join(DATA, "RUL_FD001.txt"))
    assert pdf["rul_real"].tolist() == ref["rul_real"].tolist() and len(pdf) == 100


def test_plausibility_rules_hold(spark):
    df = (
        spark.read.format("csv")
        .option("sep", " ")
        .schema(READINGS_SCHEMA)
        .load([os.path.join(DATA, "train_FD001.txt"), os.path.join(DATA, "test_FD001.txt")])
    )
    for name, rule in plausibility_rules().items():
        assert df.filter(~F.expr(rule)).count() == 0, name


def _subset(n_units=6):
    tr = C.read_cmapss(os.path.join(DATA, "train_FD001.txt"))
    return tr[tr.unit <= n_units].reset_index(drop=True)


def test_rul_and_rolling_features_match(spark):
    tr = _subset()
    ref = C.rolling_features(C.add_rul_train(tr))
    sdf = spark.createDataFrame(tr.assign(split="train"))
    out = (
        SF.add_rolling_features(SF.add_rul_train(sdf))
        .toPandas()
        .sort_values(["unit", "cycle"])
        .reset_index(drop=True)
    )
    assert (out["rul"].to_numpy() == ref["rul"].to_numpy()).all()
    for c in C.feature_columns()[1:]:
        np.testing.assert_allclose(out[c].to_numpy(), ref[c].to_numpy(), rtol=1e-6, atol=1e-6, err_msg=c)


def test_test_split_has_no_rul(spark):
    tr = _subset(2)
    out = SF.add_rul_train(spark.createDataFrame(tr.assign(split="test"))).toPandas()
    assert out["rul"].isna().all()


def test_health_index_matches(spark):
    tr = C.read_cmapss(os.path.join(DATA, "train_FD001.txt"))
    tr = tr[tr.unit <= 30].reset_index(drop=True)
    pp = C.fit_health_params(tr)
    hi = C.health_index(tr, pp)
    sdf = spark.createDataFrame(tr.assign(split="train"))
    params = SF.health_params(sdf)
    row = params.toPandas().iloc[0]
    for _, r in pp.iterrows():
        assert row[f"mu_{r.sensor}"] == pytest.approx(r.mu, rel=1e-9)
        assert row[f"sd_{r.sensor}"] == pytest.approx(r.sd, rel=1e-6)
        assert row[f"w_{r.sensor}"] == pytest.approx(r.w, rel=1e-6, abs=1e-9)
    assert row["norm"] == pytest.approx(float(pp["norm"].iloc[0]), rel=1e-6)
    curve = SF.health_curve(sdf, params).toPandas().sort_values(["unit", "cycle"]).reset_index(drop=True)
    np.testing.assert_allclose(curve["health_index"].to_numpy(), hi.to_numpy(), rtol=1e-6, atol=1e-8)
