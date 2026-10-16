"""Linear projection of the health index up to the failure threshold (pure, no dependencies)."""

from __future__ import annotations

import math

MIN_POINTS = 5  # fewer points than this: no trend is fitted
MIN_SLOPE = 1e-6  # a flat or falling trend never crosses the threshold
PROJECTION_STEPS = 40  # approximate number of points drawn on the projected segment


def project_health(
    points: list[tuple[int, float]], threshold: float = 1.0, window: int = 30, max_ahead: int = 250
) -> dict:
    """Fits a least-squares line to the last `window` points (cycle, index) and extrapolates
    it until it crosses `threshold`.

    Returns {"slope", "intercept", "cycles_to_threshold", "cross_cycle", "projection"}.
    If the slope is not positive (or there are fewer than MIN_POINTS points) nothing is
    projected: it is better to say there is no trend than to invent a crossing.
    """
    pts = points[-window:]
    out = {
        "slope": None,
        "intercept": None,
        "cycles_to_threshold": None,
        "cross_cycle": None,
        "projection": [],
    }
    if len(pts) < MIN_POINTS:
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
    if slope <= MIN_SLOPE:
        return out
    ahead = (threshold - current) / slope
    if ahead <= 0:  # already above the threshold
        out["cycles_to_threshold"], out["cross_cycle"] = 0, last_c
        out["projection"] = [(last_c, round(current, 4))]
        return out
    ahead = min(ahead, max_ahead)
    cycles_ahead = math.ceil(ahead)
    out["cycles_to_threshold"] = cycles_ahead
    out["cross_cycle"] = last_c + cycles_ahead
    step = max(1, int(ahead // PROJECTION_STEPS))
    cyc = list(range(last_c, last_c + cycles_ahead + 1, step))
    if cyc[-1] != out["cross_cycle"]:
        cyc.append(out["cross_cycle"])
    out["projection"] = [(c, round(intercept + slope * c, 4)) for c in cyc]
    return out
