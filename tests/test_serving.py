"""Tests for the validated serving wrapper (audit finding D-02).

Covers both layers: the wrapper's own validation logic (fast, in-process) and
a real MLflow save/load round trip (mlflow.pyfunc.save_model -> load_model),
so the tests exercise the same path a Databricks Model Serving endpoint uses,
without requiring a live workspace.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

mlflow = pytest.importorskip("mlflow")
pytest.importorskip("sklearn")

from sklearn.linear_model import LinearRegression

from lib.cmapss import feature_columns
from lib.serving import ValidatedRULModel, log_validated_model

FEATS = feature_columns()
N_FEATS = len(FEATS)


def _valid_frame(n: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    df = pd.DataFrame(rng.normal(size=(n, N_FEATS)), columns=FEATS)
    df["cycle"] = np.arange(1, n + 1, dtype=float)
    return df


def _fitted_model() -> LinearRegression:
    X = _valid_frame(20)
    y = np.arange(20, dtype=float)
    return LinearRegression().fit(X, y)


# ---------------------------------------------------------------------------
# In-process wrapper validation (no MLflow round trip)
# ---------------------------------------------------------------------------


def _wrapper() -> ValidatedRULModel:
    w = ValidatedRULModel(feature_columns=FEATS)
    w.model = _fitted_model()
    return w


def test_valid_frame_scores_successfully():
    w = _wrapper()
    out = w.predict(None, _valid_frame(5))
    assert len(out) == 5
    assert all(math.isfinite(v) for v in out)


def test_rejects_all_null_row():
    X = _valid_frame(1)
    X.loc[:, :] = float("nan")
    with pytest.raises(ValueError, match="null or non-finite"):
        _wrapper().predict(None, X)


def test_rejects_one_null_value():
    X = _valid_frame(2)
    X.loc[0, "s2"] = float("nan")
    with pytest.raises(ValueError, match="null or non-finite"):
        _wrapper().predict(None, X)


def test_rejects_nan_value():
    X = _valid_frame(2)
    X.loc[0, "s3"] = float("nan")
    with pytest.raises(ValueError, match="null or non-finite"):
        _wrapper().predict(None, X)


def test_rejects_inf_value():
    X = _valid_frame(2)
    X.loc[0, "s4"] = float("inf")
    with pytest.raises(ValueError, match="null or non-finite"):
        _wrapper().predict(None, X)


def test_rejects_missing_columns():
    X = _valid_frame(2).drop(columns=["s7"])
    with pytest.raises(ValueError, match="missing feature columns"):
        _wrapper().predict(None, X)


def test_rejects_extra_columns():
    X = _valid_frame(2)
    X["unexpected_sensor"] = 1.0
    with pytest.raises(ValueError, match="unexpected feature columns"):
        _wrapper().predict(None, X)


def test_rejects_duplicate_columns():
    X = _valid_frame(2)
    X = pd.concat([X, X[["s2"]]], axis=1)  # duplicate "s2" column
    with pytest.raises(ValueError, match="duplicate feature columns"):
        _wrapper().predict(None, X)


def test_rejects_non_numeric_column():
    X = _valid_frame(2)
    X["s2"] = ["a", "b"]
    with pytest.raises(ValueError, match="not numeric"):
        _wrapper().predict(None, X)


def test_rejects_cycle_below_one():
    X = _valid_frame(2)
    X.loc[0, "cycle"] = 0
    with pytest.raises(ValueError, match="cycle must be"):
        _wrapper().predict(None, X)


def test_rejects_empty_frame():
    X = _valid_frame(0)
    with pytest.raises(ValueError, match="no rows"):
        _wrapper().predict(None, X)


def test_column_order_does_not_matter_for_a_well_formed_frame():
    # The wrapper must score by name, not by positional order: a frame with
    # a permuted but complete, correct column set must still work and match
    # the inner model scored on the canonical order.
    X = _valid_frame(3)
    shuffled = X[list(reversed(FEATS))]
    w = _wrapper()
    out_canonical = w.predict(None, X)
    out_shuffled = w.predict(None, shuffled)
    assert np.allclose(out_canonical, out_shuffled)


# ---------------------------------------------------------------------------
# Real MLflow save/load round trip (what a serving endpoint actually runs)
# ---------------------------------------------------------------------------


def test_real_mlflow_round_trip_rejects_invalid_and_accepts_valid(tmp_path):
    model = _fitted_model()
    example = _valid_frame(5)
    local_dir = tmp_path / "validated_model"

    python_model = ValidatedRULModel()
    import json

    inner_path = tmp_path / "inner_model"
    mlflow.sklearn.save_model(model, str(inner_path))
    feat_path = tmp_path / "feature_columns.json"
    feat_path.write_text(json.dumps(FEATS))

    mlflow.pyfunc.save_model(
        path=str(local_dir),
        python_model=python_model,
        artifacts={"inner_model": str(inner_path), "feature_columns": str(feat_path)},
        input_example=example,
    )

    loaded = mlflow.pyfunc.load_model(str(local_dir))

    # Valid input: scores, and matches the raw inner model exactly.
    good_out = loaded.predict(example)
    assert np.allclose(np.asarray(good_out), model.predict(example[FEATS]))

    # Invalid input: the served model raises instead of returning a number.
    bad = example.copy()
    bad.loc[0, :] = None
    with pytest.raises(Exception, match="null or non-finite"):
        loaded.predict(bad)


def test_log_validated_model_helper_builds_a_working_wrapper(tmp_path):
    mlflow.set_tracking_uri(f"sqlite:///{tmp_path / 'mlflow.db'}")
    mlflow.set_experiment("test-validated-serving")
    model = _fitted_model()
    example = _valid_frame(5)
    with mlflow.start_run():
        info = log_validated_model(model, FEATS, input_example=example)
    loaded = mlflow.pyfunc.load_model(info.model_uri)
    out = loaded.predict(example)
    assert np.allclose(np.asarray(out), model.predict(example[FEATS]))
    bad = example.copy()
    bad.loc[0, "s2"] = float("inf")
    with pytest.raises(Exception, match="null or non-finite"):
        loaded.predict(bad)
