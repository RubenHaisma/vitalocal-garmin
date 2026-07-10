"""Derived health analytics over real Garmin series — the informative layer.

Three things forecast.py doesn't do, all computed from the user's OWN data:
  - recovery_score: a composite readiness (Garmin's own came back empty for this
    device) from HRV / resting-HR / sleep / body-battery vs personal baseline.
  - training_load: acute-vs-chronic workload ratio (ACWR) from daily intensity
    minutes — the standard overreaching/injury-risk signal.
  - drivers: lagged correlations showing which behaviours actually move HRV / RHR.

Sparse, honest by design: a component with <4 real points is skipped, a
correlation with <5 paired days is skipped — nothing is invented to fill a gap.
"""
from __future__ import annotations

import statistics
from typing import Any

MIN_COMPONENT = 4
MIN_PAIRS = 5


def _vals(days: list[dict], key: str) -> list[float]:
    out = []
    for d in days:
        v = d.get(key)
        if v is not None:
            try:
                out.append(float(v))
            except (TypeError, ValueError):
                pass
    return out


# ---------------- recovery score ----------------

def _component(days: list[dict], key: str, higher_better: bool, weight: float, label: str) -> dict | None:
    vals = _vals(days, key)
    if len(vals) < MIN_COMPONENT:
        return None
    latest, base = vals[-1], vals[:-1]
    mu = statistics.mean(base)
    sd = statistics.pstdev(base) or 1e-9
    z = (latest - mu) / sd
    if not higher_better:
        z = -z
    score = max(0.0, min(100.0, 50 + z * 20))  # +1σ in the good direction ≈ 70
    return {
        "key": key, "label": label, "latest": round(latest, 1), "baseline": round(mu, 1),
        "z": round(z, 2), "score": round(score), "weight": weight, "higher_better": higher_better,
    }


def recovery_score(days: list[dict]) -> dict | None:
    spec = [
        ("hrv", True, 0.35, "HRV"),
        ("resting_hr", False, 0.25, "Resting HR"),
        ("sleep_score", True, 0.20, "Sleep"),
        ("body_battery_high", True, 0.20, "Body Battery"),
    ]
    comps = [c for c in (_component(days, *s) for s in spec) if c]
    if not comps:
        return None
    tw = sum(c["weight"] for c in comps)
    score = round(sum(c["score"] * c["weight"] for c in comps) / tw)
    band = "recovered" if score >= 67 else "moderate" if score >= 45 else "strained"
    drag = min(comps, key=lambda c: c["score"])
    return {
        "score": score, "band": band, "components": comps,
        "drag": drag["label"] if drag["score"] < 50 else None,
        "n": len(comps),
    }


# ---------------- training load (ACWR) ----------------

def _worn(d: dict) -> bool:
    """A day the watch was actually on — so intensity 0 means 'rested', not 'no data'.
    Without this, unworn days count as zero load and wreck the ACWR baseline."""
    return any(d.get(k) is not None for k in ("steps", "hrv", "resting_hr", "sleep_score"))


def training_load(days: list[dict]) -> dict | None:
    if len(days) < 7:
        return None
    im = [float(d.get("intensity_minutes") or 0) for d in days]
    n = len(days)

    def window_mean(last: int) -> tuple[float | None, int]:
        lo = n - last
        vals = [im[i] for i in range(n) if i >= lo and _worn(days[i])]
        return (statistics.mean(vals) if vals else None), len(vals)

    acute, na = window_mean(7)
    chronic, nc = window_mean(28)

    # ACWR needs enough real wear in both windows to mean anything
    if acute is None or chronic is None or nc < 6 or na < 2 or chronic <= 0:
        return {
            "acute": round(acute, 1) if acute is not None else None,
            "chronic": round(chronic, 1) if chronic is not None else None,
            "acwr": None, "zone": "insufficient",
            "label": f"need more consistent wear ({nc} of 28 days worn)",
            "unit": "intensity min/day", "coverage": nc,
        }

    acwr = acute / chronic
    if acwr < 0.8:
        zone, label = "low", "detraining / low load"
    elif acwr <= 1.3:
        zone, label = "optimal", "optimal load — sweet spot"
    elif acwr <= 1.5:
        zone, label = "caution", "ramping fast — caution"
    else:
        zone, label = "high", "load spike — elevated strain/injury risk"

    return {
        "acute": round(acute, 1), "chronic": round(chronic, 1), "acwr": round(acwr, 2),
        "zone": zone, "label": label, "unit": "intensity min/day", "coverage": nc,
    }


# ---------------- drivers (lagged correlations) ----------------

def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < MIN_PAIRS:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def _paired(days: list[dict], pred_key: str, targ_key: str, lag: int) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for t in range(len(days)):
        s = t - lag
        if s < 0:
            continue
        pv, tv = days[s].get(pred_key), days[t].get(targ_key)
        if pv is not None and tv is not None:
            try:
                xs.append(float(pv))
                ys.append(float(tv))
            except (TypeError, ValueError):
                pass
    return xs, ys


def _strength(r: float) -> str:
    a = abs(r)
    return "strong" if a >= 0.5 else "moderate" if a >= 0.3 else "weak"


def drivers(days: list[dict]) -> dict:
    #  predictor_key, label, lag (0 = same day, 1 = prior day)
    specs = {
        "hrv": ("HRV", [
            ("sleep_hours", "Sleep that night", 0),
            ("intensity_minutes", "Prior-day training", 1),
            ("stress_avg", "Stress same day", 0),
            ("steps", "Prior-day steps", 1),
        ]),
        "resting_hr": ("Resting HR", [
            ("intensity_minutes", "Prior-day training", 1),
            ("sleep_hours", "Sleep that night", 0),
            ("stress_avg", "Stress same day", 0),
            ("steps", "Prior-day steps", 1),
        ]),
    }
    out: dict[str, Any] = {}
    for targ, (tlabel, plist) in specs.items():
        rows = []
        for pk, lab, lag in plist:
            xs, ys = _paired(days, pk, targ, lag)
            r = _pearson(xs, ys)
            if r is None:
                continue
            rows.append({"predictor": pk, "label": lab, "r": round(r, 2), "n": len(xs), "strength": _strength(r)})
        rows.sort(key=lambda x: -abs(x["r"]))
        if rows:
            out[targ] = {"label": tlabel, "n": max(x["n"] for x in rows), "drivers": rows}
    return out


def compute(data: dict) -> dict:
    days = data.get("days", [])
    return {
        "recovery": recovery_score(days),
        "training_load": training_load(days),
        "drivers": drivers(days),
    }
