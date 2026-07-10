"""Statistical forecasting + trend/anomaly analysis over real Garmin series.

No black-box ML and no invented data: a metric with too few real points simply
gets no forecast (the UI shows an empty state). Everything here is derived from
the user's own history — linear trend by least squares, residual-based
confidence bands that widen with horizon, direction judged against each metric's
"good" polarity, and a z-score anomaly flag on the latest reading.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

# key -> (label, unit, good_direction). good_direction: +1 higher-is-better, -1 lower-is-better
METRICS: dict[str, tuple[str, str, int]] = {
    "hrv": ("HRV", "ms", +1),
    "resting_hr": ("Resting HR", "bpm", -1),
    "sleep_hours": ("Sleep", "h", +1),
    "sleep_score": ("Sleep Score", "", +1),
    "body_battery_high": ("Body Battery", "", +1),
    "stress_avg": ("Stress", "", -1),
    "steps": ("Steps", "", +1),
    "intensity_minutes": ("Intensity", "min", +1),
}

MIN_POINTS = 4  # below this we do NOT forecast — an honest empty state beats a fabricated line


def _series(days: list[dict], key: str) -> tuple[np.ndarray, np.ndarray]:
    """Return (x_index, y_value) for the real, non-null points of a metric."""
    xs, ys = [], []
    for i, d in enumerate(days):
        v = d.get(key)
        if v is not None:
            try:
                ys.append(float(v))
                xs.append(i)
            except (TypeError, ValueError):
                pass
    return np.array(xs, dtype=float), np.array(ys, dtype=float)


def _dec(key: str) -> int:
    return 1 if key == "sleep_hours" else 0


def forecast_metric(days: list[dict], key: str, horizon: int = 7) -> dict[str, Any] | None:
    xs, ys = _series(days, key)
    n = int(ys.size)
    if n < MIN_POINTS:
        return None

    # least-squares linear trend
    A = np.vstack([xs, np.ones_like(xs)]).T
    slope, intercept = np.linalg.lstsq(A, ys, rcond=None)[0]
    resid = ys - (slope * xs + intercept)
    sigma = float(np.std(resid, ddof=1)) if n > 2 else float(np.std(resid))
    sigma = max(sigma, 1e-9)

    last_idx = len(days) - 1
    last_date = date.fromisoformat(days[-1]["date"])
    dec = _dec(key)

    def clamp(v: float) -> float:
        # keep forecasts physically plausible (no negative HR/steps/etc.)
        # float() strips numpy types so the result is JSON-serializable downstream
        v = max(float(v), 0.0)
        if key in ("sleep_score", "body_battery_high", "stress_avg"):
            v = min(v, 100.0)
        if key == "sleep_hours":
            v = min(v, 14.0)
        return v

    fc = []
    for h in range(1, horizon + 1):
        x = last_idx + h
        mean = slope * x + intercept
        band = 1.96 * sigma * np.sqrt(1.0 + h / max(n, 1))  # widen with horizon
        fc.append({
            "date": (last_date + timedelta(days=h)).isoformat(),
            "mean": round(clamp(mean), dec),
            "lo": round(clamp(mean - band), dec),
            "hi": round(clamp(mean + band), dec),
        })

    win = min(7, n)
    recent = float(np.mean(ys[-win:]))
    prior = float(np.mean(ys[:-win])) if n > win else recent
    weekly_change = float(slope * 7)
    good_dir = METRICS[key][2]

    ref = max(abs(recent), 1.0)
    if abs(weekly_change) < 0.04 * ref:
        trend, improving = "stable", None
    else:
        trend = "up" if weekly_change > 0 else "down"
        improving = (weekly_change > 0) == (good_dir > 0)

    latest_z = float(resid[-1] / sigma)
    confidence = "low" if n < 7 else ("medium" if n < 14 else "high")

    return {
        "key": key,
        "label": METRICS[key][0],
        "unit": METRICS[key][1],
        "good_dir": good_dir,
        "n": n,
        "dec": dec,
        "recent_mean": round(recent, dec),
        "prior_mean": round(prior, dec),
        "slope_per_day": round(float(slope), 3),
        "weekly_change": round(weekly_change, dec),
        "trend": trend,
        "improving": improving,
        "anomaly": abs(latest_z) >= 2.0,
        "latest_z": round(latest_z, 2),
        "confidence": confidence,
        "forecast": fc,
    }


def analyze(data: dict, horizon: int = 7) -> dict[str, Any]:
    """Per-metric forecast + a compact signal summary for the LLM and the header."""
    days = data.get("days", [])
    metrics: dict[str, Any] = {}
    for key in METRICS:
        f = forecast_metric(days, key, horizon)
        if f:
            metrics[key] = f

    improving = [m["label"] for m in metrics.values() if m["improving"] is True]
    declining = [m["label"] for m in metrics.values() if m["improving"] is False]
    anomalies = [m["label"] for m in metrics.values() if m["anomaly"]]
    n_days = sum(1 for d in days if d.get("steps") is not None or d.get("hrv") is not None)

    return {
        "horizon": horizon,
        "metrics": metrics,
        "signals": {
            "improving": improving,
            "declining": declining,
            "anomalies": anomalies,
            "days_with_data": n_days,
            "span_days": len(days),
        },
    }
