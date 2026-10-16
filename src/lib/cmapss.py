"""Pure utilities for NASA C-MAPSS FD001 (pandas/numpy, no Spark).

This module is the reference implementation: the Spark pipeline (src/pipeline/) implements
the same definitions with native window functions, and the tests check the definitions
here. Any change of definition must be made in both places.

Data source: Saxena et al. (2008), NASA Ames Prognostics Data Repository.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
SETTINGS = ["setting1", "setting2", "setting3"]
SENSORS = [f"s{i}" for i in range(1, 22)]
COLUMNS = ["unit", "cycle", *SETTINGS, *SENSORS]  # 26 columns

# FD001 has a single operating condition and a single fault mode (HPC degradation).
# Columns that are constant in train and test (standard deviation exactly 0): setting3,
# s1, s5, s10, s16, s18, s19. s6 takes only two values (21.60 and 21.61, std 0.0014):
# quantisation noise with no information, so it is dropped as well.
# tests/test_cmapss.py checks this against the real data.
CONSTANT_SENSORS = ["s1", "s5", "s6", "s10", "s16", "s18", "s19"]
CONSTANT_SETTINGS = ["setting3"]
ACTIVE_SENSORS = [s for s in SENSORS if s not in CONSTANT_SENSORS]  # 14 sensors
# setting1 and setting2 vary only by noise (FD001 is a single regime): they are kept in
# silver but are not model features.
KEPT_SETTINGS = ["setting1", "setting2"]

# Training RUL clip. Standard practice in the C-MAPSS literature: degradation is not
# observable early in life, so the RUL is modelled as piece-wise linear with a ceiling of
# 125 cycles (e.g. Heimes, 2008; Li, Ding and Sun, 2018, "Remaining useful life estimation
# in prognostics using deep convolutional neural networks").
RUL_CLIP = 125

WINDOWS = (5, 10)
SLOPE_WINDOW = 10
HEALTHY_CYCLES = 30  # first cycles of every engine = "healthy" reference
END_CYCLES = 30  # last cycles of every train engine = "failure" reference
SLOPE_EPS = 1e-12  # below this the least-squares denominator is treated as zero (slope 0)


def feature_columns(sensors: Iterable[str] = ACTIVE_SENSORS, include_cycle: bool = True) -> list[str]:
    """Model feature names, in a stable order."""
    cols = ["cycle"] if include_cycle else []
    cols += list(sensors)
    for s in sensors:
        for w in WINDOWS:
            cols += [f"{s}_mean{w}", f"{s}_std{w}"]
        cols.append(f"{s}_slope{SLOPE_WINDOW}")
    return cols


def sensors_of_feature(name: str, sensors: Iterable[str] = ACTIVE_SENSORS) -> str | None:
    """Sensor a feature belongs to ('s9_mean5' -> 's9'); None for 'cycle'."""
    for s in sorted(sensors, key=len, reverse=True):
        if name == s or name.startswith(s + "_"):
            return s
    return None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def parse_line(line: str) -> dict | None:
    """Parses one line of a C-MAPSS train/test file.

    The files are space separated with trailing spaces. Returns None for blank lines;
    raises ValueError when the field count is not 26 or a value is not numeric.
    """
    parts = line.split()
    if not parts:
        return None
    if len(parts) != len(COLUMNS):
        raise ValueError(f"expected {len(COLUMNS)} fields, got {len(parts)}: {line[:60]!r}")
    row: dict = {}
    for name, raw in zip(COLUMNS, parts):
        row[name] = int(raw) if name in ("unit", "cycle") else float(raw)
    return row


def parse_text(text: str) -> pd.DataFrame:
    rows = [r for r in (parse_line(line) for line in text.splitlines()) if r is not None]
    return pd.DataFrame(rows, columns=COLUMNS)


def read_cmapss(path: str) -> pd.DataFrame:
    with open(path, encoding="ascii") as fh:
        return parse_text(fh.read())


def parse_rul_text(text: str) -> pd.DataFrame:
    """RUL_FD001.txt: one value per line; line i is the actual RUL of test engine i."""
    vals = [int(tok) for tok in text.split()]
    return pd.DataFrame({"unit": range(1, len(vals) + 1), "rul_real": vals})


def read_rul(path: str) -> pd.DataFrame:
    with open(path, encoding="ascii") as fh:
        return parse_rul_text(fh.read())


def find_constant_columns(df: pd.DataFrame, columns: Iterable[str], tol: float = 1e-9) -> list[str]:
    """Columns whose standard deviation is <= tol."""
    return [c for c in columns if float(df[c].std(ddof=0)) <= tol]


# --------------------------------------------------------------------------
# RUL
# --------------------------------------------------------------------------
def add_rul_train(df: pd.DataFrame, clip: int | None = RUL_CLIP) -> pd.DataFrame:
    """Adds rul_raw (= last cycle of the engine - cycle) and rul (clipped at `clip`)."""
    out = df.copy()
    max_cycle = out.groupby("unit")["cycle"].transform("max")
    out["rul_raw"] = max_cycle - out["cycle"]
    out["rul"] = out["rul_raw"].clip(upper=clip) if clip is not None else out["rul_raw"]
    return out


def last_cycle(df: pd.DataFrame) -> pd.DataFrame:
    """Row of the last cycle of every engine."""
    idx = df.groupby("unit")["cycle"].idxmax()
    return df.loc[idx].sort_values("unit").reset_index(drop=True)


# --------------------------------------------------------------------------
# Rolling features
# --------------------------------------------------------------------------
def _rolling_slope(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    """Least-squares slope of y against x over a rolling window of `window` rows.

    With a single observation the slope is 0. Computed from rolling sums so that it
    matches the Spark version exactly.
    """
    n = x.rolling(window, min_periods=1).count()
    sx = x.rolling(window, min_periods=1).sum()
    sy = y.rolling(window, min_periods=1).sum()
    sxy = (x * y).rolling(window, min_periods=1).sum()
    sxx = (x * x).rolling(window, min_periods=1).sum()
    den = n * sxx - sx * sx
    num = n * sxy - sx * sy
    slope = num / den.where(den.abs() > SLOPE_EPS)
    return slope.fillna(0.0)


def rolling_features(df: pd.DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS) -> pd.DataFrame:
    """Rolling features per engine and cycle.

    For every sensor: mean and sample standard deviation (ddof=1) over windows of 5 and
    10 cycles, and the slope over 10 cycles. Windows look backwards only (past plus the
    current cycle) with min_periods=1, so no future information leaks. The standard
    deviation of a single value is 0.
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
            slopes.append(
                _rolling_slope(
                    x.iloc[idx].reset_index(drop=True), d[s].iloc[idx].reset_index(drop=True), SLOPE_WINDOW
                ).set_axis(idx)
            )
        new_cols[f"{s}_slope{SLOPE_WINDOW}"] = pd.concat(slopes).sort_index()
    parts.append(pd.DataFrame(new_cols))
    return pd.concat(parts, axis=1)


# --------------------------------------------------------------------------
# Health index (curve shown in the app)
# --------------------------------------------------------------------------
def fit_health_params(train: pd.DataFrame, sensors: Iterable[str] = ACTIVE_SENSORS) -> pd.DataFrame:
    """Health index parameters, fitted on train only.

    For every sensor: mean (mu) and standard deviation (sd) over the first HEALTHY_CYCLES
    cycles of every engine ("healthy"); m = mean z-score over the last END_CYCLES cycles of
    every engine (signed shift towards failure). The weight is w = m. `norm` is the mean
    raw index at end of life, so that the failure threshold equals 1.0.
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
    """Health index: 0 = healthy (start of life), 1 = mean level at failure.

    Linear combination of z-scores against the healthy reference, weighted by each
    sensor's shift towards failure, normalised and smoothed with a rolling mean of
    `smooth` cycles per engine. It is not a prediction: it is a summary view of the
    sensors to visualise the trend.
    """
    d = df.sort_values(["unit", "cycle"])
    hi = _raw_health(d, params) / float(params["norm"].iloc[0])
    sm = hi.groupby(d["unit"]).transform(lambda s: s.rolling(smooth, min_periods=1).mean())
    return sm.reindex(df.index)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------
def rmse(y_true, y_pred) -> float:
    a = np.asarray(y_true, dtype=float)
    b = np.asarray(y_pred, dtype=float)
    return float(math.sqrt(np.mean((b - a) ** 2)))


def phm08_score(y_true, y_pred) -> float:
    """Asymmetric score of the PHM08 challenge (Saxena et al., 2008).

    d = predicted - actual. If d < 0 (early prediction): exp(-d/13) - 1.
    If d >= 0 (late prediction, the dangerous side): exp(d/10) - 1. Summed over the
    engines; lower is better.
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
# State values written by the first deployment (Spanish schema), kept readable as a fallback.
LEGACY_STATES = {"critico": "critical", "vigilar": "watch", "sano": "healthy"}
ACTION_PREFIX = "Demo estimate; requires engineering review. "


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
    return LEGACY_STATES.get(s, s)


def recommended_action(state: str, rul: float, sensors: Iterable[str] = ()) -> str:
    """Advisory text for an engine, from its state, estimated RUL and top sensors.

    It is a statistical estimate from an experimental demo model, not a maintenance order:
    every text says that engineering review is required and none of them instructs a
    shutdown or replacement. The model's uncertainty is not calibrated (no prediction
    intervals); the typical error is only the test RMSE shown in the app.
    """
    n = round(max(float(rul), 0.0))
    sens = [s for s in sensors if s][:2]
    state = normalize_state(state)
    if state == "critical":
        txt = ACTION_PREFIX + f"Estimated RUL is low (about {n} cycles): consider prioritising an inspection."
        if sens:
            txt += " Sensors to check first: " + " and ".join(sens) + "."
        return txt
    if state == "watch":
        return ACTION_PREFIX + (
            f"Estimated RUL is moderate (about {n} cycles): "
            "consider closer monitoring and planning an inspection."
        )
    return ACTION_PREFIX + "No degradation flagged by the model; keep the usual maintenance plan."


def occlusion_contributions(
    predict_fn: Callable[[pd.DataFrame], np.ndarray],
    X: pd.DataFrame,
    baseline: pd.Series,
    sensors: Iterable[str] = ACTIVE_SENSORS,
) -> pd.DataFrame:
    """Local contribution of every sensor by occlusion.

    For every row and sensor, ALL the features derived from that sensor (value, means,
    standard deviations, slope) are replaced by the healthy reference (`baseline`) and the
    increase of the predicted RUL is measured: contrib = pred(without the sensor) - pred(original).
    Positive = that sensor is pushing the RUL down. This is not SHAP: contributions are not
    additive and the substitution can create unrealistic combinations; it is a local
    sensitivity, useful for ranking.
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
    """For every row, the k sensors with the largest contribution (descending)."""
    res = []
    for _, row in contrib.iterrows():
        top = row.sort_values(ascending=False).head(k)
        res.append([(str(i), float(v)) for i, v in top.items()])
    return res
