"""Data preparation, models and evaluation (pandas + scikit-learn, no Spark)."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from lib.cmapss import (
    RUL_CLIP,
    add_rul_train,
    feature_columns,
    last_cycle,
    phm08_score,
    rmse,
    rolling_features,
)

SEED = 42
CANDIDATE_MODEL = "hist_gradient_boosting"

# Hyperparameters chosen with GroupKFold by engine on train ONLY (see scripts/train_local.py
# --tune); the test set is never used to tune them.
HGB_PARAMS = {
    "max_iter": 300,
    "learning_rate": 0.05,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": SEED,
}
RIDGE_ALPHA = 10.0


def make_models(hgb_params: dict | None = None) -> dict:
    """Candidate model and two baselines (from simplest to most complex)."""
    return {
        "baseline_mean": DummyRegressor(strategy="mean"),
        "baseline_ridge": make_pipeline(StandardScaler(), Ridge(alpha=RIDGE_ALPHA)),
        CANDIDATE_MODEL: HistGradientBoostingRegressor(**(hgb_params or HGB_PARAMS)),
    }


def build_frames(
    train_raw: pd.DataFrame, test_raw: pd.DataFrame, rul_df: pd.DataFrame, clip: int = RUL_CLIP
) -> dict:
    """Features and labels from the raw files (pandas version of the gold layer)."""
    feats = feature_columns()
    tr = rolling_features(add_rul_train(train_raw, clip))
    te = rolling_features(test_raw)
    te_last = last_cycle(te).merge(rul_df, on="unit", how="left")
    return {
        "feature_columns": feats,
        "X_train": tr[feats].astype("float64"),
        "y_train": tr["rul"].astype("float64"),
        "groups": tr["unit"],
        "train_frame": tr,
        "test_frame": te,
        "X_test": te_last[feats].astype("float64"),
        "y_test": te_last["rul_real"].astype("float64"),
        "test_units": te_last["unit"],
    }


def validation_rmse_by_engine(
    model_factory: Callable[[], object],
    train_frame: pd.DataFrame,
    feats: list[str],
    n_splits: int = 5,
    seed: int = SEED,
    max_cut_rul: int = 145,
) -> dict:
    """Validation RMSE by engine using train ONLY (GroupKFold by `unit`).

    This is the metric that decides hyperparameters and the champion: the FD001 test set
    does not take part. Every held-out engine is evaluated in two ways:
    - `rmse_val_truncated`: its series is cut at a cycle chosen with a fixed seed so that the
      actual (unclipped) RUL lies between 0 and `max_cut_rul`, mimicking the test evaluation
      (last observed cycle of a truncated engine). No information from the test set is used.
    - `rmse_val_all_cycles`: out-of-fold RMSE over every cycle with the clipped label.
    `std_folds` is the standard deviation of the truncated RMSE across folds: an improvement
    smaller than that should not be considered real.

    `model_factory` is a zero-argument callable that returns a fresh estimator.
    `train_frame` must contain `unit`, `cycle`, `rul` (clipped) and the `feats` columns.
    """
    tf = train_frame.sort_values(["unit", "cycle"]).reset_index(drop=True)
    X, y, groups = tf[feats].astype("float64"), tf["rul"].astype("float64"), tf["unit"].to_numpy()
    rng = np.random.default_rng(seed)
    max_cycle = tf.groupby("unit")["cycle"].max()
    # Deterministic cut per engine (depends only on the seed and the series length).
    cut_rul = {int(u): int(rng.integers(0, min(int(mx) - 1, max_cut_rul) + 1)) for u, mx in max_cycle.items()}

    oof = np.full(len(tf), np.nan)
    fold_rmse, yt_all, yp_all = [], [], []
    for tri, vai in GroupKFold(n_splits).split(X, y, groups):
        model = model_factory()
        model.fit(X.iloc[tri], y.iloc[tri])
        oof[vai] = model.predict(X.iloc[vai])
        held = tf.iloc[vai]
        yt, yp = [], []
        for u, du in held.groupby("unit"):
            r = cut_rul[int(u)]
            row = du[du["cycle"] == int(max_cycle[u]) - r]
            yp.append(float(np.clip(model.predict(row[feats].astype("float64")), 0.0, None)[0]))
            yt.append(float(r))
        fold_rmse.append(rmse(yt, yp))
        yt_all.extend(yt)
        yp_all.extend(yp)
    return {
        "rmse_val_truncated": rmse(yt_all, yp_all),
        "rmse_val_all_cycles": rmse(y, np.clip(oof, 0.0, None)),
        "std_folds": float(np.std(fold_rmse)),
        "n_splits": int(n_splits),
        "n_engines": len(cut_rul),
    }


def promotion_decision(
    champion_version: int | None,
    rmse_val_champion: float | None,
    rmse_val_candidate: float,
    std_folds: float,
    n_splits: int = 5,
) -> tuple[bool, str]:
    """Promotion rule for the `champion` alias WITHOUT using the test set.

    - No champion: the first version is promoted.
    - Champion without a recorded validation RMSE (versions older than this protocol): not
      promoted; the decision is manual (the test RMSE is never used to decide).
    - Promoted only if the validation RMSE by engine improves by more than the standard error
      of the fold mean (`std_folds / sqrt(n_splits)`), so that noise does not move the alias.
    Returns (promote, reason).
    """
    if champion_version is None:
        return True, "no champion yet: the first version is promoted"
    if rmse_val_champion is None:
        return False, (
            f"champion v{champion_version} has no recorded validation RMSE: the new version stays as "
            "challenger; promotion requires a manual decision (the test set is not used to decide)"
        )
    margin = max(float(std_folds), 0.0) / float(np.sqrt(max(int(n_splits), 1)))
    if float(rmse_val_candidate) < float(rmse_val_champion) - margin:
        return True, (
            f"improves the validation RMSE by engine: {rmse_val_candidate:.3f} < {rmse_val_champion:.3f} - "
            f"{margin:.3f} (champion v{champion_version})"
        )
    return False, (
        f"no significant improvement of the validation RMSE: {rmse_val_candidate:.3f} against "
        f"{rmse_val_champion:.3f} (margin {margin:.3f}, champion v{champion_version})"
    )


def evaluate(model, X_test: pd.DataFrame, y_true) -> dict:
    """RMSE and PHM08 score on the last cycle of every test engine, against the actual RUL.

    Predictions are clipped at >= 0. The actual RUL of RUL_FD001.txt is not clipped while
    the model is trained with the RUL clipped at 125: that mismatch is the usual one in the
    literature and penalises engines with a long remaining life.
    """
    pred = np.clip(np.asarray(model.predict(X_test), dtype=float), 0.0, None)
    return {
        "rmse_test": rmse(y_true, pred),
        "phm08_test": phm08_score(y_true, pred),
        "mae_test": float(np.mean(np.abs(pred - np.asarray(y_true, dtype=float)))),
    }
