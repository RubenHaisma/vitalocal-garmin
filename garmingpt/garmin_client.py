"""Garmin Connect data access via python-garminconnect (cyberjunky).

Why not raw `garth`: garth is deprecated, talks to Garmin over plain `requests`
with a fixed mobile UA, and Garmin now 429-blocks that TLS fingerprint.
`garminconnect` ≥0.3 ships its own `curl_cffi` client that impersonates a real
browser's TLS handshake, so it authenticates cleanly. Same session model as
before: log in once (MFA handled), tokens persist under SESSION_DIR, every later
run resumes without another login.

This is still the THROWAWAY validation layer — the real product uses Garmin's
official OAuth Health API; this module gets deleted the day those keys arrive.
"""
from __future__ import annotations

import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore", message=".*[Gg]arth is deprecated.*")

from garminconnect import Garmin  # noqa: E402

try:
    from garminconnect import GarminConnectAuthenticationError  # noqa: E402
except Exception:  # noqa: BLE001 — older/newer layouts
    GarminConnectAuthenticationError = Exception  # type: ignore

SESSION_DIR = Path.home() / ".garmingpt" / "tokens"


def _prompt_mfa() -> str:
    return input("Garmin MFA code (check email / authenticator): ").strip()


def login(email: str, password: str) -> None:
    """Authenticate and persist the session. Prompts for an MFA code only if Garmin asks."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    g = Garmin(email=email, password=password, prompt_mfa=_prompt_mfa)
    g.login(str(SESSION_DIR))  # curl_cffi impersonation → no 429; dumps tokens on success


def has_session() -> bool:
    """True if a saved token dir exists (may still be expired — a data call proves it)."""
    return SESSION_DIR.exists() and any(SESSION_DIR.iterdir())


def client() -> Garmin:
    """Return an authenticated client by resuming the saved session."""
    if not has_session():
        raise RuntimeError("No saved Garmin session — sign in first.")
    g = Garmin()
    g.login(str(SESSION_DIR))  # loads tokens; refreshes silently if near expiry
    return g


# ---- browser login (two-step, so MFA works without a terminal) ----
_PENDING: Garmin | None = None


def web_login(email: str, password: str) -> str:
    """Start a login from the web form. Returns 'ok' (done) or 'mfa' (code needed).

    Raises RuntimeError('bad-credentials') on a wrong email/password.
    """
    global _PENDING
    if not email or not password:
        raise RuntimeError("missing-credentials")
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    g = Garmin(email=email.strip(), password=password, return_on_mfa=True)
    try:
        status, _ = g.login()  # falls through 429s to a working strategy
    except GarminConnectAuthenticationError:
        raise RuntimeError("bad-credentials")
    if status == "needs_mfa":
        _PENDING = g
        return "mfa"
    g.client.dump(str(SESSION_DIR))
    _PENDING = None
    return "ok"


def web_login_mfa(code: str) -> str:
    """Finish a login that required a 2-factor code."""
    global _PENDING
    if _PENDING is None:
        raise RuntimeError("no-pending-login")
    if not code or not code.strip():
        raise RuntimeError("missing-code")
    _PENDING.resume_login({}, code.strip())  # state is held on the client; {} is ignored
    _PENDING.client.dump(str(SESSION_DIR))
    _PENDING = None
    return "ok"


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(days: int = 90) -> dict[str, list[dict]]:
    """Pull the last `days` of daily metrics for the SQLite `sync` / `brief` path.

    Returns {metric: [rows]} shaped so store.py keys by day and baselines.py's
    flexible field extraction still resolves every metric. One failing day never
    aborts the sync.
    """
    g = client()
    today = date.today()
    out: dict[str, list[dict]] = {
        m: [] for m in ("daily_summary", "daily_sleep", "daily_hrv",
                        "daily_stress", "daily_steps", "training_readiness")
    }

    def _sleep_score(sl: Any) -> float | None:
        dto = (sl or {}).get("dailySleepDTO") or {}
        scores = dto.get("sleepScores") or {}
        return _num((scores.get("overall") or {}).get("value"))

    def _hrv_avg(hv: Any) -> float | None:
        return _num(((hv or {}).get("hrvSummary") or {}).get("lastNightAvg"))

    def _readiness(tr: Any) -> float | None:
        rows = tr if isinstance(tr, list) else ([tr] if tr else [])
        for r in rows:
            s = _num((r or {}).get("score"))
            if s is not None:
                return s
        return None

    n = 0
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        try:
            s = g.get_stats_and_body(d) or {}
        except Exception:  # noqa: BLE001 — surface via count, don't abort
            s = {}
        if s:
            s.setdefault("calendar_date", d)
            if s.get("sleepingSeconds") is not None:  # baselines reads snake_case
                s.setdefault("sleeping_seconds", s["sleepingSeconds"])
            out["daily_summary"].append(s)
            out["daily_steps"].append({"calendar_date": d, "totalSteps": s.get("totalSteps")})
            n += 1

        try:
            out["daily_sleep"].append({"calendar_date": d, "value": _sleep_score(g.get_sleep_data(d))})
        except Exception:  # noqa: BLE001
            pass
        try:
            out["daily_hrv"].append({"calendar_date": d, "lastNightAvg": _hrv_avg(g.get_hrv_data(d))})
        except Exception:  # noqa: BLE001
            pass
        try:
            out["training_readiness"].append({"calendar_date": d, "score": _readiness(g.get_training_readiness(d))})
        except Exception:  # noqa: BLE001
            pass
        if i % 10 == 0:
            print(f"  · {d}")

    print(f"  fetched {n}/{days} days")
    return out
