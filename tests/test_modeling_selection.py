"""Protocolo de selección sin test: validación por motor solo con train y regla de promoción."""
import os

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from lib import cmapss as C
from lib.modeling import promotion_decision, validation_rmse_by_engine

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "cmapss")


@pytest.fixture(scope="module")
def train_frame():
    raw = C.read_cmapss(os.path.join(DATA, "train_FD001.txt"))
    raw = raw[raw["unit"] <= 20]  # 20 motores reales: suficiente y rápido
    return C.rolling_features(C.add_rul_train(raw, C.RUL_CLIP))


def test_validation_usa_solo_train_y_es_determinista(train_frame):
    feats = C.feature_columns()
    r1 = validation_rmse_by_engine(lambda: Ridge(alpha=10.0), train_frame, feats, n_splits=4, seed=7)
    r2 = validation_rmse_by_engine(lambda: Ridge(alpha=10.0), train_frame, feats, n_splits=4, seed=7)
    assert r1 == r2  # misma semilla, mismo corte, mismo resultado
    assert r1["n_engines"] == 20 and r1["n_splits"] == 4
    assert 0 < r1["rmse_val_truncated"] < 125 and 0 < r1["rmse_val_all_cycles"] < 125
    assert r1["std_folds"] >= 0
    r3 = validation_rmse_by_engine(lambda: Ridge(alpha=10.0), train_frame, feats, n_splits=4, seed=8)
    assert r3["rmse_val_truncated"] != r1["rmse_val_truncated"]  # otro corte, otra cifra


def test_validation_no_evalua_un_motor_con_su_propio_pliegue(train_frame):
    """Si el modelo memoriza por motor, la validación por grupos debe castigarlo."""
    feats = C.feature_columns()

    class Memoriza:
        def fit(self, X, y):
            self.tabla = dict(zip(map(tuple, np.round(X.to_numpy(), 6)), y.to_numpy()))
            self.media = float(np.mean(y))
            return self

        def predict(self, X):
            return np.array([self.tabla.get(tuple(r), self.media) for r in np.round(X.to_numpy(), 6)])

    r = validation_rmse_by_engine(Memoriza, train_frame, feats, n_splits=4)
    # Fuera de pliegue, el modelo que memoriza solo puede devolver la media: error grande.
    assert r["rmse_val_all_cycles"] > 30


def test_promotion_decision_sin_test():
    assert promotion_decision(None, None, 18.0, 1.0) == (True, "no habia champion: se asigna a la primera version")
    ok, motivo = promotion_decision(1, None, 17.0, 1.0)
    assert ok is False and "decisión manual" in motivo and "test" in motivo
    ok, motivo = promotion_decision(1, 18.0, 17.8, 1.0, n_splits=4)  # mejora 0.2 < margen 1/sqrt(4)=0.5
    assert ok is False and "no mejora" in motivo
    ok, motivo = promotion_decision(1, 18.0, 17.0, 1.0, n_splits=4)  # mejora 1.0 > margen 0.5
    assert ok is True and "mejora" in motivo
    ok, _ = promotion_decision(1, 18.0, 17.9, 0.0)  # sin margen: mejora estricta
    assert ok is True


def test_02_train_no_decide_champion_con_el_test():
    src = open(os.path.join(os.path.dirname(__file__), "..", "src", "02_train.py"), encoding="utf-8").read()
    assert "promotion_decision(" in src and "rmse_val_de_version" in src
    assert 'cand["rmse_test"] <' not in src and "rmse_de_version(" not in src
    assert 'select("rmse_test").first()' not in src
