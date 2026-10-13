import math
import os

import numpy as np
import pandas as pd
import pytest

from lib import cmapss as C

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "cmapss")
LINE = ("1 1 -0.0007 -0.0004 100.0 518.67 641.82 1589.70 1400.60 14.62 21.61 554.36 "
        "2388.06 9046.19 1.30 47.47 521.66 2388.02 8138.62 8.4195 0.03 392 2388 100.00 39.06 23.4190  ")


def _toy(n_units=2, n=12, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for u in range(1, n_units + 1):
        for c in range(1, n + 1 + u):
            rows.append({"unit": u, "cycle": c, **{s: float(rng.normal(c * 0.1 * (i % 3), 1.0)) for i, s in enumerate(C.SENSORS)}})
    return pd.DataFrame(rows)


# ---------------- parseo ----------------
def test_parse_line_con_espacios_finales():
    r = C.parse_line(LINE)
    assert len(r) == 26 and r["unit"] == 1 and r["cycle"] == 1 and isinstance(r["unit"], int)
    assert r["setting1"] == pytest.approx(-0.0007) and r["s21"] == pytest.approx(23.4190)


def test_parse_line_vacia_y_errores():
    assert C.parse_line("   \n") is None
    with pytest.raises(ValueError):
        C.parse_line("1 2 3")
    with pytest.raises(ValueError):
        C.parse_line(LINE.replace("518.67", "abc"))


def test_parse_rul_text():
    df = C.parse_rul_text("112 \n98 \n69 \n")
    assert df.to_dict("list") == {"unit": [1, 2, 3], "rul_real": [112, 98, 69]}


@pytest.mark.skipif(not os.path.exists(os.path.join(DATA, "train_FD001.txt")), reason="sin datos")
def test_conteos_de_los_datos_reales():
    tr = C.read_cmapss(os.path.join(DATA, "train_FD001.txt"))
    te = C.read_cmapss(os.path.join(DATA, "test_FD001.txt"))
    rul = C.read_rul(os.path.join(DATA, "RUL_FD001.txt"))
    assert (tr.shape, tr["unit"].nunique()) == ((20631, 26), 100)
    assert (te.shape, te["unit"].nunique()) == ((13096, 26), 100)
    assert len(rul) == 100
    # los 7 sensores y el ajuste que el proyecto descarta son constantes (o casi) en train
    exactos = C.find_constant_columns(tr, C.SENSORS + C.SETTINGS, tol=1e-9)
    assert set(exactos) == {"s1", "s5", "s10", "s16", "s18", "s19", "setting3"}
    casi = C.find_constant_columns(tr, ["s6"], tol=0.01)
    assert casi == ["s6"] and tr["s6"].nunique() == 2
    assert set(C.CONSTANT_SENSORS) == set(exactos) - {"setting3"} | {"s6"}
    assert len(C.ACTIVE_SENSORS) == 14
    # los activos no son constantes
    assert not C.find_constant_columns(tr, C.ACTIVE_SENSORS, tol=1e-9)


# ---------------- RUL ----------------
def test_rul_train_y_recorte():
    df = pd.DataFrame({"unit": [1] * 200 + [2] * 10, "cycle": list(range(1, 201)) + list(range(1, 11))})
    out = C.add_rul_train(df, clip=125)
    u1 = out[out.unit == 1]
    assert u1["rul_raw"].iloc[0] == 199 and u1["rul"].iloc[0] == 125
    assert u1["rul_raw"].iloc[-1] == 0 and u1["rul"].iloc[-1] == 0
    assert (u1["rul"] <= 125).all()
    u2 = out[out.unit == 2]
    assert u2["rul"].tolist() == list(range(9, -1, -1))  # sin recorte en motores cortos
    assert C.add_rul_train(df, clip=None)["rul"].max() == 199


def test_last_cycle():
    df = pd.DataFrame({"unit": [2, 1, 1, 2, 2], "cycle": [1, 1, 2, 2, 3], "x": [1, 2, 3, 4, 5]})
    out = C.last_cycle(df)
    assert out["unit"].tolist() == [1, 2] and out["cycle"].tolist() == [2, 3] and out["x"].tolist() == [3, 5]


# ---------------- variables moviles ----------------
def test_rolling_features_valores_exactos():
    s = [1.0, 3.0, 2.0, 6.0, 4.0, 5.0, 9.0, 7.0, 8.0, 10.0, 12.0, 11.0]
    df = pd.DataFrame({"unit": 1, "cycle": range(1, 13), "sx": s})
    out = C.rolling_features(df, sensors=["sx"])
    # media 5 del ciclo 7 = media de ciclos 3..7
    assert out.loc[6, "sx_mean5"] == pytest.approx(np.mean(s[2:7]))
    # std muestral 5 del ciclo 7
    assert out.loc[6, "sx_std5"] == pytest.approx(np.std(s[2:7], ddof=1))
    # primera fila: std = 0 y media = valor
    assert out.loc[0, "sx_std5"] == 0.0 and out.loc[0, "sx_mean5"] == 1.0
    # media 10 del ciclo 12 = ciclos 3..12
    assert out.loc[11, "sx_mean10"] == pytest.approx(np.mean(s[2:12]))
    # pendiente 10 del ciclo 12 = polyfit sobre ciclos 3..12
    assert out.loc[11, "sx_slope10"] == pytest.approx(np.polyfit(range(3, 13), s[2:12], 1)[0])
    assert out.loc[0, "sx_slope10"] == 0.0


def test_rolling_sin_fuga_entre_motores_ni_futuro():
    df = _toy()
    out = C.rolling_features(df, sensors=["s2"])
    # cambiar el futuro no altera el pasado
    df2 = df.copy()
    df2.loc[(df2.unit == 1) & (df2.cycle > 8), "s2"] += 1000
    out2 = C.rolling_features(df2, sensors=["s2"])
    past = (out.unit == 1) & (out.cycle <= 8)
    pd.testing.assert_frame_equal(out[past].reset_index(drop=True), out2[past].reset_index(drop=True))
    # el motor 2 no cambia
    m2 = out.unit == 2
    pd.testing.assert_frame_equal(out[m2].reset_index(drop=True), out2[m2].reset_index(drop=True))


def test_feature_columns():
    cols = C.feature_columns()
    assert len(cols) == 1 + 14 + 14 * 5 and len(set(cols)) == len(cols)
    assert C.sensors_of_feature("s14_std10") == "s14" and C.sensors_of_feature("cycle") is None
    assert C.sensors_of_feature("s2_mean5") == "s2"


# ---------------- métricas ----------------
def test_phm08_asimetrica():
    assert C.phm08_score([50], [50]) == 0.0
    temprana = C.phm08_score([50], [40])    # d = -10
    tardia = C.phm08_score([50], [60])      # d = +10
    assert temprana == pytest.approx(math.exp(10 / 13) - 1)
    assert tardia == pytest.approx(math.exp(10 / 10) - 1)
    assert tardia > temprana  # llegar tarde se penaliza mas
    assert C.phm08_score([10, 20], [12, 18]) == pytest.approx(math.exp(0.2) - 1 + math.exp(2 / 13) - 1)


def test_rmse():
    assert C.rmse([1, 2, 3], [1, 2, 3]) == 0.0
    assert C.rmse([0, 0], [3, 4]) == pytest.approx(math.sqrt(12.5))


def test_classify_state_umbrales():
    assert C.classify_state(5) == "critical" and C.classify_state(20) == "critical"
    assert C.classify_state(20.1) == "watch" and C.classify_state(35) == "watch"
    assert C.classify_state(35.1) == "healthy"
    assert C.classify_state(30, critical=40, watch=50) == "critical"


# ---------------- indicador de salud y contribuciones ----------------
def test_indicador_de_salud_sube_con_la_degradacion():
    rng = np.random.default_rng(1)
    rows = []
    for u in range(1, 21):
        n = 120 + u
        for c in range(1, n + 1):
            deg = (c / n) ** 2
            rows.append({"unit": u, "cycle": c, "a": 10 + 5 * deg + rng.normal(0, .2),
                         "b": 3 - 2 * deg + rng.normal(0, .2), "c": rng.normal(0, 1)})
    df = pd.DataFrame(rows)
    p = C.fit_health_params(df, sensors=["a", "b", "c"])
    hi = C.health_index(df, p)
    first = hi[df.cycle <= 10].mean()
    last = df.assign(hi=hi).groupby("unit").tail(10)["hi"].mean()
    assert abs(first) < 0.2 and 0.8 < last < 1.2
    # los pesos de a y b tienen signos opuestos (a sube, b baja); el ruido pesa poco
    w = p.set_index("sensor")["w"]
    assert w["a"] > 0 > w["b"] and abs(w["c"]) < 0.3


def test_occlusion_contributions_modelo_lineal():
    sensors = ["s2", "s3"]
    X = pd.DataFrame({"cycle": [10.0], "s2": [5.0], "s2_mean5": [5.0], "s3": [1.0], "s3_mean5": [1.0]})
    base = pd.Series({"cycle": 5.0, "s2": 1.0, "s2_mean5": 1.0, "s3": 1.0, "s3_mean5": 1.0})
    predict = lambda d: 100 - 2.0 * d["s2"] - 2.0 * d["s2_mean5"] - 3.0 * d["s3"]
    c = C.occlusion_contributions(predict, X, base, sensors=sensors)
    assert c.loc[0, "s2"] == pytest.approx(2 * 4 + 2 * 4)   # sin s2 la RUL sube 16
    assert c.loc[0, "s3"] == pytest.approx(0.0)              # s3 ya estaba en su valor sano
    top = C.top_k_sensors(c, k=2)
    assert top[0][0][0] == "s2" and len(top[0]) == 2


def test_normalize_state_y_accion():
    assert [C.normalize_state(s) for s in ("critico", "vigilar", "sano", "watch")] == ["critical", "watch", "healthy", "watch"]
    a = C.recommended_action("critico", 12.4, ["s4", "s11", "s2"])
    assert a == "Schedule shutdown and replacement within 12 cycles. Inspect s4 and s11 now."
    assert "within the 30-cycle window" in C.recommended_action("watch", 30.0)
    assert C.recommended_action("healthy", 90).startswith("Normal operation")
    assert C.recommended_action("critical", -3, []) == "Schedule shutdown and replacement within 0 cycles."
