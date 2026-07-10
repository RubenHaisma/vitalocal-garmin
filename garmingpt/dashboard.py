"""Rich Garmin export for the dashboard, via python-garminconnect (cyberjunky).

`garminconnect` is a higher-level wrapper over `garth` — same auth, far more
endpoints (activities, body battery, VO2max, training readiness, …). It reuses
the *same* saved session `garmin_client.login` already wrote, so no second login.

    uv run python -m garmingpt dashboard            # last 30 days → dashboard_data.json

Field names vary across Garmin firmware/accounts, so every extraction tries
several candidate keys and tolerates gaps rather than guessing one name — same
philosophy as baselines.py. One failing metric never aborts the export.
"""
from __future__ import annotations

import json
import re
import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

warnings.filterwarnings("ignore", message=".*[Gg]arth is deprecated.*")

from garminconnect import Garmin  # noqa: E402

from . import garmin_client  # noqa: E402 — reuse its SESSION_DIR

OUT = Path(__file__).parent / "dashboard_data.json"


def _looks_like_id(s: Any) -> bool:
    """True for GUID / long hex handles (Garmin's default displayName), which are not names."""
    return bool(s) and re.fullmatch(r"[0-9a-fA-F-]{16,}", str(s).strip()) is not None


def _connect() -> Garmin:
    """Resume the authenticated garminconnect session (curl_cffi, no 429)."""
    return garmin_client.client()


def _first(d: Any, *keys: str) -> Any:
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def _round(v: Any, n: int = 1) -> float | None:
    f = _num(v)
    return round(f, n) if f is not None else None


def _sleep_fields(sleep: Any) -> tuple[float | None, float | None]:
    """(hours, score) from a get_sleep_data payload."""
    dto = _first(sleep, "dailySleepDTO") or {}
    secs = _first(dto, "sleepTimeSeconds") or _first(sleep, "sleepTimeSeconds")
    hours = _round(_num(secs) / 3600, 2) if _num(secs) else None
    scores = _first(dto, "sleepScores") or {}
    score = _num(_first(_first(scores, "overall") or {}, "value")) or _num(_first(dto, "sleepScoreValue"))
    return hours, score


def _hrv_avg(hrv: Any) -> float | None:
    summ = _first(hrv, "hrvSummary") or {}
    return _num(_first(summ, "lastNightAvg", "weeklyAvg"))


def _readiness_score(tr: Any) -> float | None:
    rows = tr if isinstance(tr, list) else ([tr] if tr else [])
    for r in rows:
        s = _num(_first(r, "score"))
        if s is not None:
            return s
    return None


def _day_record(g: Garmin, cdate: str) -> dict:
    """One day's normalized metrics. Each source is grabbed independently."""
    rec: dict[str, Any] = {"date": cdate}

    def grab(fn: Callable[[], Any], apply: Callable[[Any], None]) -> None:
        try:
            apply(fn())
        except Exception:  # noqa: BLE001 — a missing metric is a gap, not a failure
            pass

    def _stats(s: Any) -> None:
        rec["steps"] = _num(_first(s, "totalSteps"))
        rec["calories"] = _num(_first(s, "totalKilocalories"))
        rec["active_calories"] = _num(_first(s, "activeKilocalories"))
        rec["resting_hr"] = _num(_first(s, "restingHeartRate"))
        rec["stress_avg"] = _num(_first(s, "averageStressLevel"))
        rec["body_battery_high"] = _num(_first(s, "bodyBatteryHighestValue"))
        rec["body_battery_low"] = _num(_first(s, "bodyBatteryLowestValue"))
        rec["floors"] = _num(_first(s, "floorsAscended"))
        mod = _num(_first(s, "moderateIntensityMinutes")) or 0
        vig = _num(_first(s, "vigorousIntensityMinutes")) or 0
        rec["intensity_minutes"] = mod + 2 * vig  # Garmin weights vigorous 2×
        rec["weight"] = _round(_num(_first(s, "weight")) / 1000, 1) if _num(_first(s, "weight")) else None

    grab(lambda: g.get_stats_and_body(cdate), _stats)

    def _sleep(s: Any) -> None:
        h, sc = _sleep_fields(s)
        rec["sleep_hours"], rec["sleep_score"] = h, sc

    grab(lambda: g.get_sleep_data(cdate), _sleep)
    grab(lambda: g.get_hrv_data(cdate), lambda h: rec.__setitem__("hrv", _hrv_avg(h)))
    grab(lambda: g.get_training_readiness(cdate), lambda t: rec.__setitem__("readiness", _readiness_score(t)))
    return rec


def _activities(g: Garmin, n: int = 15) -> list[dict]:
    try:
        raw = g.get_activities(0, n) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for a in raw:
        dist_m = _num(_first(a, "distance"))
        dur_s = _num(_first(a, "duration", "elapsedDuration", "movingDuration"))
        out.append({
            "name": _first(a, "activityName") or "Activity",
            "type": _first(_first(a, "activityType") or {}, "typeKey") or "other",
            "date": (_first(a, "startTimeLocal") or "")[:10],
            "distance_km": _round(dist_m / 1000, 2) if dist_m else None,
            "duration_min": _round(dur_s / 60, 0) if dur_s else None,
            "avg_hr": _num(_first(a, "averageHR")),
            "max_hr": _num(_first(a, "maxHR")),
            "calories": _num(_first(a, "calories")),
        })
    return out


def _vo2max(g: Garmin, today: str) -> dict:
    try:
        rows = g.get_max_metrics(today) or []
    except Exception:  # noqa: BLE001
        return {}
    row = rows[0] if isinstance(rows, list) and rows else rows
    gen = _first(row, "generic") or {}
    cyc = _first(row, "cycling") or {}
    return {
        "running": _num(_first(gen, "vo2MaxPreciseValue", "vo2MaxValue")),
        "cycling": _num(_first(cyc, "vo2MaxPreciseValue", "vo2MaxValue")),
    }


def export(days: int = 30) -> dict:
    g = _connect()
    today = date.today()

    profile = {}
    try:
        # The socialProfile endpoint carries the real name (fullName). Note
        # get_user_profile() hits a different path that only has the GUID
        # displayName, which is NOT a name and must never be shown.
        try:
            sp = g.connectapi("/userprofile-service/socialProfile") or {}
        except Exception:  # noqa: BLE001
            sp = g.get_user_profile() or {}
        name = (_first(sp, "fullName", "userProfileFullName") or "").strip()
        if _looks_like_id(name):
            name = ""
        if not name:  # last resort: first + last from settings
            try:
                bio = _first(g.get_userprofile_settings() or {}, "userData") or {}
                name = " ".join(x for x in (_first(bio, "firstName"), _first(bio, "lastName")) if x).strip()
            except Exception:  # noqa: BLE001
                name = ""
        profile = {"name": name or None, "id": _first(sp, "profileId", "id")}
    except Exception:  # noqa: BLE001
        pass

    print(f"Pulling {days} days of daily metrics…")
    day_records = []
    for i in range(days):
        cdate = (today - timedelta(days=i)).isoformat()
        rec = _day_record(g, cdate)
        day_records.append(rec)
        if i % 5 == 0:
            print(f"  · {cdate}")
    day_records.reverse()  # oldest → newest for charting

    data = {
        "generated": today.isoformat(),
        "profile": profile,
        "days": day_records,
        "vo2max": _vo2max(g, today.isoformat()),
        "activities": _activities(g),
    }
    OUT.write_text(json.dumps(data, indent=2, default=str))
    n_days = sum(1 for d in day_records if d.get("steps") is not None)
    print(f"Wrote {OUT}  ({n_days}/{days} days with data, {len(data['activities'])} activities)")
    return data


if __name__ == "__main__":
    export()
