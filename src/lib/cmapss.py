"""Utilidades puras para NASA C-MAPSS FD001 (pandas/numpy, sin Spark).

Esta lógica es la referencia: el pipeline de Spark (src/pipeline/) implementa las
mismas definiciones con funciones de ventana nativas, y los tests comprueban las
definiciones aquí. Cualquier cambio de definición debe hacerse en ambos sitios.

Fuente de los datos: Saxena et al. (2008), NASA Ames Prognostics Data Repository.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Esquema
# --------------------------------------------------------------------------
SETTINGS = ["setting1", "setting2", "setting3"]
SENSORS = [f"s{i}" for i in range(1, 22)]
COLUMNS = ["unit", "cycle"] + SETTINGS + SENSORS  # 26 columnas

# FD001 tiene una sola condición de operación y un solo modo de fallo (HPC).
# Columnas constantes en train y test (desviación tipica exactamente 0): setting3,
# s1, s5, s10, s16, s18, s19. s6 toma solo dos valores (21.60 y 21.61, desviación
# 0.0014): es ruido de cuantizacion sin información, se descarta también.
# Los scripts de verificacion de los tests (test_cmapss.py) lo comprueban con los datos.
CONSTANT_SENSORS = ["s1", "s5", "s6", "s10", "s16", "s18", "s19"]
CONSTANT_SETTINGS = ["setting3"]
ACTIVE_SENSORS = [s for s in SENSORS if s not in CONSTANT_SENSORS]  # 14 sensores
# Los ajustes setting1 y setting2 varian solo por ruido (FD001 es un único regimen):
# se conservan en silver pero no se usan como variables del modelo.
KEPT_SETTINGS = ["setting1", "setting2"]

# Recorte de la RUL de entrenamiento. Practica estándar en la literatura de C-MAPSS:
# la degradacion no es observable al inicio de la vida, así que se modela la RUL como
# "piece-wise linear" con techo de 125 ciclos (p. ej. Heimes, 2008; Li, Ding y Sun, 2018,
# "Remaining useful life estimation in prognostics using deep convolutional neural networks").
RUL_CLIP = 125

WINDOWS = (5, 10)
SLOPE_WINDOW = 10
HEALTHY_CYCLES = 30  # primeros ciclos de cada motor = referencia "sano"
END_CYCLES = 30      # últimos ciclos de cada motor de train = referencia "fallo"


def feature_columns(sensors: Iterable[str] = ACTIVE_SENSORS, include_cycle: bool = True) -> list[str]:
    """Nombres de las variables del modelo, en orden estable."""
    cols = ["cycle"] if include_cycle else []
    cols += list(sensors)
    for s in sensors:
        for w in WINDOWS:
            cols += [f"{s}_mean{w}", f"{s}_std{w}"]
        cols.append(f"{s}_slope{SLOPE_WINDOW}")
    return cols


def sensors_of_feature(name: str, sensors: Iterable[str] = ACTIVE_SENSORS) -> Optional[str]:
    """Sensor al que pertenece una variable ('s9_mean5' -> 's9'); None para 'cycle'."""
    for s in sorted(sensors, key=len, reverse=True):
        if name == s or name.startswith(s + "_"):
            return s
    return None


# --------------------------------------------------------------------------
# Parseo
# --------------------------------------------------------------------------
def parse_line(line: str) -> Optional[dict]:
    """Parsea una linea de train/test de C-MAPSS.

    Los ficheros usan espacio como separador y tienen espacios finales. Devuelve
    None para lineas vacias; lanza ValueError si el número de campos no es 26 o
    algún valor no es numerico.
    """
    parts = line.split()
    if not parts:
        return None
    if len(parts) != len(COLUMNS):
        raise ValueError(f"se esperaban {len(COLUMNS)} campos y hay {len(parts)}: {line[:60]!r}")
    row: dict = {}
    for name, raw in zip(COLUMNS, parts):
        row[name] = int(raw) if name in ("unit", "cycle") else float(raw)
    return row


def parse_text(text: str) -> pd.DataFrame:
    rows = [r for r in (parse_line(l) for l in text.splitlines()) if r is not None]
    return pd.DataFrame(rows, columns=COLUMNS)


def read_cmapss(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="ascii") as fh:
        return parse_text(fh.read())


def parse_rul_text(text: str) -> pd.DataFrame:
    """RUL_FD001.txt: un valor por linea; la linea i corresponde al motor i del test."""
    vals = [int(tok) for tok in text.split()]
    return pd.DataFrame({"unit": range(1, len(vals) + 1), "rul_real": vals})


def read_rul(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="ascii") as fh:
        return parse_rul_text(fh.read())


def find_constant_columns(df: pd.DataFrame, columns: Iterable[str], tol: float = 1e-9) -> list[str]:
    """Columnas con desviación tipica <= tol."""
    return [c for c in columns if float(df[c].std(ddof=0)) <= tol]


# --------------------------------------------------------------------------
# RUL
# --------------------------------------------------------------------------
def add_rul_train(df: pd.DataFrame, clip: Optional[int] = RUL_CLIP) -> pd.DataFrame:
    """Anade rul_raw (= último ciclo del motor - ciclo) y rul (recortada a `clip`)."""
    out = df.copy()
    max_cycle = out.groupby("unit")["cycle"].transform("max")
    out["rul_raw"] = max_cycle - out["cycle"]
    out["rul"] = out["rul_raw"].clip(upper=clip) if clip is not None else out["rul_raw"]
    return out


def last_cycle(df: pd.DataFrame) -> pd.DataFrame:
    """Fila del último ciclo de cada motor."""
    idx = df.groupby("unit")["cycle"].idxmax()
    return df.loc[idx].sort_values("unit").reset_index(drop=True)


# --------------------------------------------------------------------------
# Variables moviles
# --------------------------------------------------------------------------
def _rolling_slope(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    """Pendiente de y frente a x en una ventana movil de `window` filas (minimos cuadrados).

    Con una sola observacion la pendiente es 0. Se calcula con sumas moviles para que
    coincida exactamente con la version de Spark.
    """
    n = x.rolling(window, min_periods=1).count()
    sx = x.rolling(window, min_periods=1).sum()
    sy = y.rolling(window, min_periods=1).sum()
    sxy = (x * y).rolling(window, min_periods=1).sum()
    sxx = (x * x).rolling(window, min_periods=1).sum()
    den = n * sxx - sx * sx
    num = n * sxy - sx * sy
    slope = num / den.where(den.abs() > 1e-12)
    return slope.fillna(0.0)


def rolling_features(df: pd.DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS) -> pd.DataFrame:
    """Variables moviles por motor y ciclo.

    Para cada sensor: media y desviación tipica muestral (ddof=1) en ventanas de 5 y 10
    ciclos, y pendiente en 10 ciclos. Ventanas hacia atras (solo pasado), incluyendo el
    ciclo actual, con min_periods=1: sin fuga de información futura. La desviación con un
    solo dato vale 0.
    """
    sensors = list(sensors)
    d = df.sort_values(["unit", "cycle"]).reset_index(drop=True)
    parts = [d]
    g = d.groupby("unit", sort=False)
    new_cols: dict[str, pd.Series] = {}
    x = d["cycle"].astype(float)
    for s in sensors:
        for w in WINDOWS:
            roll = g[s].rolling(w, min_periods=1)
            new_cols[f"{s}_mean{w}"] = roll.mean().reset_index(level=0, drop=True).sort_index()
            new_cols[f"{s}_std{w}"] = (
                roll.std(ddof=1).reset_index(level=0, drop=True).sort_index().fillna(0.0)
            )
        slopes = []
        for _, idx in g.indices.items():
            slopes.append(_rolling_slope(x.iloc[idx].reset_index(drop=True),
                                         d[s].iloc[idx].reset_index(drop=True), SLOPE_WINDOW)
                          .set_axis(idx))
        new_cols[f"{s}_slope{SLOPE_WINDOW}"] = pd.concat(slopes).sort_index()
    parts.append(pd.DataFrame(new_cols))
    return pd.concat(parts, axis=1)


# --------------------------------------------------------------------------
# Indicador de salud (para la curva de la app)
# --------------------------------------------------------------------------
def fit_health_params(train: pd.DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS) -> pd.DataFrame:
    """Parametros del indicador de salud, ajustados solo con train.

    Para cada sensor: media (mu) y desviación (sd) en los primeros HEALTHY_CYCLES ciclos
    de cada motor ("sano"); m = media del z-score en los últimos END_CYCLES ciclos de
    cada motor (desplazamiento hasta el fallo, con signo). El peso es w = m. Se devuelve
    también `norm` = valor medio del indicador bruto al final de la vida (para que el
    umbral de fallo valga 1.0).
    """
    sensors = list(sensors)
    t = train.sort_values(["unit", "cycle"])
    first_n = t.groupby("unit").head(HEALTHY_CYCLES)
    last_n = t.groupby("unit").tail(END_CYCLES)
    mu = first_n[sensors].mean()
    sd = first_n[sensors].std(ddof=1).replace(0.0, np.nan)
    m = ((last_n[sensors] - mu) / sd).mean()
    params = pd.DataFrame({"sensor": sensors, "mu": mu.values, "sd": sd.values, "w": m.values})
    params["sd"] = params["sd"].fillna(1.0)
    raw_end = _raw_health(last_n, params).mean()
    params["norm"] = float(raw_end)
    return params


def _raw_health(df: pd.DataFrame, params: pd.DataFrame) -> pd.Series:
    z = (df[params["sensor"].tolist()].to_numpy() - params["mu"].to_numpy()) / params["sd"].to_numpy()
    w = params["w"].to_numpy()
    return pd.Series((z * w).sum(axis=1) / np.abs(w).sum(), index=df.index)


def health_index(df: pd.DataFrame, params: pd.DataFrame, smooth: int = 5) -> pd.Series:
    """Indicador de salud: 0 = sano (inicio de vida), 1 = nivel medio al fallo.

    Combinacion lineal de z-scores respecto a la referencia sana, ponderada por el
    desplazamiento de cada sensor hasta el fallo, normalizada y suavizada con media movil
    de `smooth` ciclos por motor. No es una predicción: es una vista resumen de los
    sensores para visualizar la tendencia.
    """
    d = df.sort_values(["unit", "cycle"])
    hi = _raw_health(d, params) / float(params["norm"].iloc[0])
    sm = hi.groupby(d["unit"]).transform(lambda s: s.rolling(smooth, min_periods=1).mean())
    return sm.reindex(df.index)


# --------------------------------------------------------------------------
# Metricas
# --------------------------------------------------------------------------
def rmse(y_true, y_pred) -> float:
    a = np.asarray(y_true, dtype=float)
    b = np.asarray(y_pred, dtype=float)
    return float(math.sqrt(np.mean((b - a) ** 2)))


def phm08_score(y_true, y_pred) -> float:
    """Score asimetrica de la competicion PHM08 (Saxena et al., 2008).

    d = predicción - real. Si d < 0 (predicción temprana): exp(-d/13) - 1.
    Si d >= 0 (predicción tardia, mas peligrosa): exp(d/10) - 1. Se suma sobre los
    motores; menor es mejor.
    """
    d = np.asarray(y_pred, dtype=float) - np.asarray(y_true, dtype=float)
    return float(np.sum(np.where(d < 0, np.exp(-d / 13.0) - 1.0, np.exp(d / 10.0) - 1.0)))


def validate_feature_frame(X: pd.DataFrame, columns: Iterable[str]) -> None:
    """Rejects input that the model must not score: missing columns, NaN/inf, cycle < 1, empty frame.

    The served model (HistGradientBoosting) accepts NaN natively and would return a number even
    for an all-null row (verified on the endpoint, see audit/02_MODEL_VALIDATION.md). Scoring code
    must therefore validate before calling it, so that a missing sensor is an explicit error
    and not a silent prediction.
    """
    cols = list(columns)
    if len(X) == 0:
        raise ValueError("no rows to score")
    missing = [c for c in cols if c not in X.columns]
    if missing:
        raise ValueError(f"missing feature columns: {missing[:5]}")
    vals = X[cols].to_numpy(dtype=float, na_value=np.nan)
    if not np.isfinite(vals).all():
        bad = [c for c, ok in zip(cols, np.isfinite(vals).all(axis=0)) if not ok]
        raise ValueError(f"null or non-finite values in: {bad[:5]}")
    if (X["cycle"].to_numpy(dtype=float) < 1).any():
        raise ValueError("cycle must be >= 1")


STATES = ("critical", "watch", "healthy")
_LEGACY_STATES = {"critico": "critical", "vigilar": "watch", "sano": "healthy"}


def classify_state(rul: float, critical: float = 20.0, watch: float = 35.0) -> str:
    """State from the estimated RUL: critical <= critical < watch <= watch < healthy."""
    if rul <= critical:
        return "critical"
    if rul <= watch:
        return "watch"
    return "healthy"


def normalize_state(state: str) -> str:
    """Maps legacy Spanish state values (critico, vigilar, sano) to the English ones."""
    s = (state or "").strip().lower()
    return _LEGACY_STATES.get(s, s)


def recommended_action(state: str, rul: float, sensors: Iterable[str] = ()) -> str:
    """Advisory text for an engine, from its state, estimated RUL and top sensors.

    It is a statistical estimate from an experimental demo model, not a maintenance order:
    every text says that engineering review is required and none of them instructs a
    shutdown or replacement. The model's uncertainty is not calibrated (no prediction
    intervals); the typical error is only the test RMSE shown in the app.
    """
    n = int(round(max(float(rul), 0.0)))
    sens = [s for s in sensors if s][:2]
    state = normalize_state(state)
    head = "Demo estimate; requires engineering review. "
    if state == "critical":
        txt = head + f"Estimated RUL is low (about {n} cycles): consider prioritising an inspection."
        if sens:
            txt += " Sensors to check first: " + " and ".join(sens) + "."
        return txt
    if state == "watch":
        return head + f"Estimated RUL is moderate (about {n} cycles): consider closer monitoring and planning an inspection."
    return head + "No degradation flagged by the model; keep the usual maintenance plan."


def occlusion_contributions(predict_fn, X: pd.DataFrame, baseline: pd.Series,
                            sensors: Iterable[str] = ACTIVE_SENSORS) -> pd.DataFrame:
    """Contribucion local de cada sensor por oclusión.

    Para cada fila y sensor se sustituyen TODAS las variables derivadas de ese sensor
    (valor, medias, desviaciones, pendiente) por las de la referencia sana (`baseline`) y se
    mide cuanto sube la RUL predicha: contrib = pred(sin el sensor) - pred(original).
    Valor positivo = ese sensor esta empujando la RUL hacia abajo. No es SHAP: no
    reparte la predicción de forma aditiva y la sustitución puede generar combinaciones
    poco realistas; es una sensibilidad local, útil para ordenar.
    """
    sensors = list(sensors)
    base_pred = np.asarray(predict_fn(X), dtype=float)
    out = {}
    for s in sensors:
        cols = [c for c in X.columns if sensors_of_feature(c, sensors) == s]
        Xo = X.copy()
        for c in cols:
            Xo[c] = float(baseline[c])
        out[s] = np.asarray(predict_fn(Xo), dtype=float) - base_pred
    return pd.DataFrame(out, index=X.index)


def top_k_sensors(contrib: pd.DataFrame, k: int = 3) -> list[list[tuple[str, float]]]:
    """Para cada fila, los k sensores con mayor contribución (descendente)."""
    res = []
    for _, row in contrib.iterrows():
        top = row.sort_values(ascending=False).head(k)
        res.append([(str(i), float(v)) for i, v in top.items()])
    return res
