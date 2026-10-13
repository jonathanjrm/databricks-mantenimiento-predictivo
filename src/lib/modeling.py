"""Preparacion de datos, modelos y evaluación (pandas + scikit-learn, sin Spark)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from lib.cmapss import (RUL_CLIP, add_rul_train, feature_columns, last_cycle,
                        phm08_score, rmse, rolling_features)

SEED = 42

# Hiperparametros elegidos con GroupKFold por motor SOLO sobre train (ver
# scripts/entrenar_local.py --tune); el test no se usa para ajustarlos.
HGB_PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40,
                  l2_regularization=1.0, early_stopping=False, random_state=SEED)


def make_models(hgb_params: dict | None = None) -> dict:
    """Modelo candidato y dos baselines (de menor a mayor complejidad)."""
    return {
        "baseline_media": DummyRegressor(strategy="mean"),
        "baseline_ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "hist_gradient_boosting": HistGradientBoostingRegressor(**(hgb_params or HGB_PARAMS)),
    }


def build_frames(train_raw: pd.DataFrame, test_raw: pd.DataFrame, rul_df: pd.DataFrame,
                 clip: int = RUL_CLIP) -> dict:
    """Variables y etiquetas a partir de los datos crudos (version pandas del gold)."""
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


def evaluate(model, X_test: pd.DataFrame, y_real) -> dict:
    """RMSE y score PHM08 sobre el último ciclo de cada motor de test, frente a la RUL real.

    Las predicciones se acotan a >= 0. La RUL real de RUL_FD001.txt no esta recortada,
    mientras que el modelo se entrena con RUL recortada a 125: ese desajuste es el habitual
    en la literatura y penaliza a los motores con vida restante larga.
    """
    pred = np.clip(np.asarray(model.predict(X_test), dtype=float), 0.0, None)
    return {"rmse_test": rmse(y_real, pred), "phm08_test": phm08_score(y_real, pred),
            "mae_test": float(np.mean(np.abs(pred - np.asarray(y_real, dtype=float))))}
