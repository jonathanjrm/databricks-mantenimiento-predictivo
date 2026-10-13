"""Proyeccion lineal del indicador de salud hasta el umbral de fallo (puro, sin dependencias)."""
from __future__ import annotations

import math


def project_health(points: list[tuple[int, float]], threshold: float = 1.0, window: int = 30,
                   max_ahead: int = 250) -> dict:
    """Ajusta una recta (minimos cuadrados) a los últimos `window` puntos (ciclo, indicador)
    y la extrapola hasta cruzar `threshold`.

    Devuelve {"slope", "intercept", "cycles_to_threshold", "cross_cycle", "projection"}.
    Si la pendiente no es positiva (o hay menos de 5 puntos) no se proyecta: es mejor decir
    que no hay tendencia que inventar un cruce.
    """
    pts = points[-window:]
    out = {"slope": None, "intercept": None, "cycles_to_threshold": None, "cross_cycle": None,
           "projection": []}
    if len(pts) < 5:
        return out
    n = len(pts)
    sx = sum(c for c, _ in pts)
    sy = sum(v for _, v in pts)
    sxx = sum(c * c for c, _ in pts)
    sxy = sum(c * v for c, v in pts)
    den = n * sxx - sx * sx
    if den == 0:
        return out
    slope = (n * sxy - sx * sy) / den
    intercept = (sy - slope * sx) / n
    out["slope"], out["intercept"] = slope, intercept
    last_c = pts[-1][0]
    current = intercept + slope * last_c
    if slope <= 1e-6:
        return out
    ahead = (threshold - current) / slope
    if ahead <= 0:  # ya esta por encima del umbral
        out["cycles_to_threshold"], out["cross_cycle"] = 0, last_c
        out["projection"] = [(last_c, round(current, 4))]
        return out
    ahead = min(ahead, max_ahead)
    out["cycles_to_threshold"] = int(math.ceil(ahead))
    out["cross_cycle"] = last_c + int(math.ceil(ahead))
    step = max(1, int(ahead // 40))
    cyc = list(range(last_c, last_c + int(math.ceil(ahead)) + 1, step))
    if cyc[-1] != out["cross_cycle"]:
        cyc.append(out["cross_cycle"])
    out["projection"] = [(c, round(intercept + slope * c, 4)) for c in cyc]
    return out
