"""Local SQLite store. One row per (metric, day); raw record kept as JSON."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB = Path.home() / ".garmingpt" / "garmin.db"


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute(
        "CREATE TABLE IF NOT EXISTS metrics "
        "(metric TEXT, day TEXT, data TEXT, PRIMARY KEY (metric, day))"
    )
    return c


def _day_of(row: dict) -> str | None:
    for k in ("calendar_date", "calendarDate", "date"):
        if row.get(k):
            return str(row[k])
    return None


def save(metric: str, rows: list[dict]) -> int:
    """Upsert all dated rows for a metric. Returns how many were written."""
    c = _conn()
    n = 0
    for r in rows:
        day = _day_of(r)
        if not day:
            continue
        c.execute(
            "INSERT OR REPLACE INTO metrics (metric, day, data) VALUES (?, ?, ?)",
            (metric, day, json.dumps(r, default=str)),
        )
        n += 1
    c.commit()
    c.close()
    return n


def load_all() -> dict[str, dict[str, dict]]:
    """Return {metric: {day: record}} for everything stored."""
    c = _conn()
    out: dict[str, dict[str, dict]] = {}
    for metric, day, data in c.execute("SELECT metric, day, data FROM metrics"):
        out.setdefault(metric, {})[day] = json.loads(data)
    c.close()
    return out
