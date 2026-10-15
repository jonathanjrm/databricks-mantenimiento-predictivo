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


def validation_rmse_by_engine(model_factory, train_frame: pd.DataFrame, feats: list[str],
                              n_splits: int = 5, seed: int = SEED, max_cut_rul: int = 145) -> dict:
    """RMSE de validación por motor usando SOLO train (GroupKFold por `unit`).

    Es la métrica con la que se debe decidir hiperparámetros y champion: el conjunto de test de
    FD001 no participa. Cada motor retenido se evalúa de dos formas:
    - `rmse_val_truncated`: se corta su serie en un ciclo elegido con una semilla fija de modo
      que la RUL real (sin recorte) quede entre 0 y `max_cut_rul`, imitando la evaluación de
      test (último ciclo observado de un motor truncado). No usa ninguna información del test.
    - `rmse_val_all_cycles`: RMSE out-of-fold sobre todos los ciclos con la etiqueta recortada.
    `std_folds` es la desviación típica del RMSE truncado entre pliegues: una mejora menor que
    esa cifra no debe considerarse real.

    `model_factory` es una función sin argumentos que devuelve un estimador nuevo.
    `train_frame` debe tener `unit`, `cycle`, `rul` (recortada) y las columnas `feats`.
    """
    from sklearn.model_selection import GroupKFold

    tf = train_frame.sort_values(["unit", "cycle"]).reset_index(drop=True)
    X, y, groups = tf[feats].astype("float64"), tf["rul"].astype("float64"), tf["unit"].to_numpy()
    rng = np.random.default_rng(seed)
    max_cycle = tf.groupby("unit")["cycle"].max()
    # Corte determinista por motor (depende solo de la semilla y de la longitud de la serie).
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
        "n_engines": int(len(cut_rul)),
    }


def promotion_decision(champ_version, rmse_val_champ, rmse_val_cand: float, std_folds: float,
                       n_splits: int = 5) -> tuple[bool, str]:
    """Regla de promoción del alias `champion` SIN usar el conjunto de test.

    - Sin champion: se promueve la primera versión.
    - Champion sin RMSE de validación registrada (versiones anteriores a este protocolo): no se
      promueve; la decisión es manual (nunca se recurre al RMSE de test para decidir).
    - Se promueve solo si la RMSE de validación por motor mejora en más del error típico de la
      media entre pliegues (`std_folds / sqrt(n_splits)`), para no mover el alias por ruido.
    Devuelve (promover, motivo).
    """
    if champ_version is None:
        return True, "no habia champion: se asigna a la primera version"
    if rmse_val_champ is None:
        return False, (f"el champion v{champ_version} no tiene RMSE de validación registrada: queda como "
                       "challenger; la promoción requiere decisión manual (no se usa el test para decidir)")
    margen = max(float(std_folds), 0.0) / float(np.sqrt(max(int(n_splits), 1)))
    if float(rmse_val_cand) < float(rmse_val_champ) - margen:
        return True, (f"mejora la RMSE de validación por motor: {rmse_val_cand:.3f} < {rmse_val_champ:.3f} - "
                      f"{margen:.3f} (champion v{champ_version})")
    return False, (f"no mejora de forma significativa la RMSE de validación: {rmse_val_cand:.3f} frente a "
                   f"{rmse_val_champ:.3f} (margen {margen:.3f}, champion v{champ_version})")


def evaluate(model, X_test: pd.DataFrame, y_real) -> dict:
    """RMSE y score PHM08 sobre el último ciclo de cada motor de test, frente a la RUL real.

    Las predicciones se acotan a >= 0. La RUL real de RUL_FD001.txt no esta recortada,
    mientras que el modelo se entrena con RUL recortada a 125: ese desajuste es el habitual
    en la literatura y penaliza a los motores con vida restante larga.
    """
    pred = np.clip(np.asarray(model.predict(X_test), dtype=float), 0.0, None)
    return {"rmse_test": rmse(y_real, pred), "phm08_test": phm08_score(y_real, pred),
            "mae_test": float(np.mean(np.abs(pred - np.asarray(y_real, dtype=float))))}
