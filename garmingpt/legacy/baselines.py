"""Normalize raw Garmin records and compute baseline deltas.

This is the part that actually matters. The whole product bet is whether
"today vs your own 28-day baseline" produces an insight worth reading. Field
names vary across Garmin firmware/accounts, so extraction tries several
candidate keys and tolerates gaps rather than guessing one name.
"""
from __future__ import annotations

import statistics
from typing import Any

METRICS = [
    "sleep_score",
    "sleep_hours",
    "resting_hr",
    "hrv",
    "stress",
    "steps",
    "body_battery",
    "training_readiness",
    "intensity_minutes",
]


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def normalize(raw: dict[str, dict[str, dict]]) -> dict[str, dict]:
    """Merge per-metric sources into one normalized record per day."""
    days: set[str] = set()
    for byday in raw.values():
        days.update(byday.keys())

    records: dict[str, dict] = {}
    for day in days:
        ds = raw.get("daily_summary", {}).get(day, {})
        sl = raw.get("daily_sleep", {}).get(day, {})
        hv = raw.get("daily_hrv", {}).get(day, {})
        sr = raw.get("daily_stress", {}).get(day, {})
        sp = raw.get("daily_steps", {}).get(day, {})
        tr = raw.get("training_readiness", {}).get(day, {})

        sleep_sec = _num(_first(ds, "sleeping_seconds", "sleep_seconds", "total_sleep_seconds"))
        mod = _num(_first(ds, "moderate_intensity_minutes", "moderateIntensityMinutes")) or 0.0
        vig = _num(_first(ds, "vigorous_intensity_minutes", "vigorousIntensityMinutes")) or 0.0

        rec = {
            "date": day,
            "sleep_score": _num(_first(sl, "value", "score")),
            "sleep_hours": round(sleep_sec / 3600, 2) if sleep_sec else None,
            "resting_hr": _num(_first(ds, "resting_heart_rate", "restingHeartRate")),
            "hrv": _num(_first(hv, "last_night_avg", "lastNightAvg", "weekly_avg")),
            "stress": _num(_first(sr, "overall_stress_level"))
            or _num(_first(ds, "average_stress_level", "averageStressLevel")),
            "steps": _num(_first(sp, "total_steps"))
            or _num(_first(ds, "total_steps", "totalSteps")),
            "body_battery": _num(
                _first(ds, "body_battery_most_recent_value", "body_battery_high_value",
                       "bodyBatteryMostRecentValue")
            ),
            "training_readiness": _num(_first(tr, "score")),
            "intensity_minutes": (mod + 2 * vig) if (mod or vig) else None,
        }
        records[day] = rec
    return records


def build_payload(records: dict[str, dict], window: int = 28) -> dict:
    """Pick the latest day with real data; compare each metric to the prior `window` days."""
    dates = sorted(records)

    def has_core(r: dict) -> bool:
        return any(r.get(k) is not None for k in ("resting_hr", "sleep_score", "steps", "hrv"))

    core = [d for d in dates if has_core(records[d])]
    if not core:
        raise RuntimeError("No usable Garmin data found — run `sync` first.")

    latest = core[-1]
    today = records[latest]
    prior = [records[d] for d in dates if d < latest][-window:]

    baselines: dict[str, dict] = {}
    for m in METRICS:
        vals = [r[m] for r in prior if r.get(m) is not None]
        cur = today.get(m)
        if not vals:
            continue
        avg = statistics.mean(vals)
        baselines[m] = {
            "today": cur,
            "avg": round(avg, 1),
            "delta": round(cur - avg, 1) if cur is not None else None,
            "n": len(vals),
        }

    last7 = [records[d] for d in dates if d <= latest][-7:]
    return {"date": latest, "today": today, "baselines": baselines, "last_7_days": last7}
