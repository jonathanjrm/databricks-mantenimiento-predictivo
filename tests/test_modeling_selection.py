"""Selection protocol without the test set: validation by engine on train only and promotion rule."""

import os

import numpy as np
import pytest
from sklearn.linear_model import Ridge

from lib import cmapss as C
from lib.modeling import promotion_decision, validation_rmse_by_engine

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "cmapss")


@pytest.fixture(scope="module")
def train_frame():
    raw = C.read_cmapss(os.path.join(DATA, "train_FD001.txt"))
    raw = raw[raw["unit"] <= 20]  # 20 real engines: enough and fast
    return C.rolling_features(C.add_rul_train(raw, C.RUL_CLIP))


def test_validation_uses_train_only_and_is_deterministic(train_frame):
    feats = C.feature_columns()
    r1 = validation_rmse_by_engine(lambda: Ridge(alpha=10.0), train_frame, feats, n_splits=4, seed=7)
    r2 = validation_rmse_by_engine(lambda: Ridge(alpha=10.0), train_frame, feats, n_splits=4, seed=7)
    assert r1 == r2  # same seed, same cut, same result
    assert r1["n_engines"] == 20 and r1["n_splits"] == 4
    assert 0 < r1["rmse_val_truncated"] < 125 and 0 < r1["rmse_val_all_cycles"] < 125
    assert r1["std_folds"] >= 0
    r3 = validation_rmse_by_engine(lambda: Ridge(alpha=10.0), train_frame, feats, n_splits=4, seed=8)
    assert r3["rmse_val_truncated"] != r1["rmse_val_truncated"]  # another cut, another figure


def test_validation_never_scores_an_engine_with_its_own_fold(train_frame):
    """If the model memorises by engine, grouped validation must penalise it."""
    feats = C.feature_columns()

    class Memorizer:
        def fit(self, X, y):
            self.table = dict(zip(map(tuple, np.round(X.to_numpy(), 6)), y.to_numpy()))
            self.mean = float(np.mean(y))
            return self

        def predict(self, X):
            return np.array([self.table.get(tuple(r), self.mean) for r in np.round(X.to_numpy(), 6)])

    r = validation_rmse_by_engine(Memorizer, train_frame, feats, n_splits=4)
    # Out of fold, the memorising model can only return the mean: large error.
    assert r["rmse_val_all_cycles"] > 30


def test_promotion_decision_without_test_set():
    assert promotion_decision(None, None, 18.0, 1.0) == (
        True,
        "no champion yet: the first version is promoted",
    )
    ok, reason = promotion_decision(1, None, 17.0, 1.0)
    assert ok is False and "manual decision" in reason and "test" in reason
    ok, reason = promotion_decision(1, 18.0, 17.8, 1.0, n_splits=4)  # gain 0.2 < margin 1/sqrt(4)=0.5
    assert ok is False and "no significant improvement" in reason
    ok, reason = promotion_decision(1, 18.0, 17.0, 1.0, n_splits=4)  # gain 1.0 > margin 0.5
    assert ok is True and "improves" in reason
    ok, _ = promotion_decision(1, 18.0, 17.9, 0.0)  # no margin: strict improvement
    assert ok is True


def test_02_train_does_not_pick_the_champion_with_the_test_set():
    path = os.path.join(os.path.dirname(__file__), "..", "src", "02_train.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "promotion_decision(" in src and "validation_rmse_of_version" in src
    assert 'cand["rmse_test"] <' not in src and "test_rmse_of_version(" not in src
    assert 'select("rmse_test").first()' not in src
