"""Local server for the GarminGPT ML dashboard.

    uv run python -m garmingpt serve            # http://127.0.0.1:8800

Serves the dashboard and a small JSON/stream API. All data is the user's real
Garmin export (dashboard_data.json); if it's missing every endpoint reports
`empty` and the UI shows an empty state — nothing is ever mocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import analytics, dashboard, forecast, garmin_client, insights

HERE = Path(__file__).parent
DATA = HERE / "dashboard_data.json"
INDEX = HERE / "static" / "index.html"

app = FastAPI(title="GarminGPT ML Dashboard")


def _load() -> dict | None:
    if not DATA.exists():
        return None
    try:
        return json.loads(DATA.read_text())
    except (json.JSONDecodeError, OSError):
        return None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(INDEX)


@app.get("/api/data")
def api_data() -> JSONResponse:
    data = _load()
    if not data or not data.get("days"):
        return JSONResponse({"empty": True})
    return JSONResponse({
        "empty": False,
        "data": data,
        "analysis": forecast.analyze(data),
        "analytics": analytics.compute(data),
    })


@app.get("/api/status")
async def api_status() -> JSONResponse:
    data = _load()
    models = await insights.available_models()
    model = await insights.pick_model()
    return JSONResponse({
        "ollama": bool(models),
        "models": models,
        "model": model,
        "logged_in": garmin_client.has_session(),
        "has_data": bool(data and data.get("days")),
        "generated": (data or {}).get("generated"),
        "days_with_data": sum(1 for d in (data or {}).get("days", []) if d.get("hrv") is not None or d.get("steps") is not None),
    })


@app.post("/api/login")
async def api_login(req: Request) -> JSONResponse:
    body = await req.json()
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""

    def _do():
        return garmin_client.web_login(email, password)

    try:
        status = await run_in_threadpool(_do)  # network + blocking → off the event loop
    except RuntimeError as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=502)
    return JSONResponse({"status": status})


@app.post("/api/login/mfa")
async def api_login_mfa(req: Request) -> JSONResponse:
    body = await req.json()
    code = body.get("code") or ""

    def _do():
        return garmin_client.web_login_mfa(code)

    try:
        status = await run_in_threadpool(_do)
    except RuntimeError as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=502)
    return JSONResponse({"status": status})


@app.get("/api/insights")
async def api_insights(model: str | None = None) -> JSONResponse:
    """Structured near-term forecast (state / predictions / risks / recommendations)."""
    data = _load()
    if not data or not data.get("days"):
        return JSONResponse({"error": "no-data"}, status_code=400)
    analysis = forecast.analyze(data)
    if not analysis["metrics"]:
        return JSONResponse({"error": "not-enough-data"}, status_code=400)
    stats = analytics.compute(data)
    try:
        result = await insights.structured_insight(data, analysis, stats, model)
    except (RuntimeError, httpx.HTTPError, ValueError) as e:
        return JSONResponse({"error": "ollama", "detail": str(e)}, status_code=502)
    return JSONResponse(result)


@app.post("/api/chat")
async def api_chat(req: Request) -> StreamingResponse:
    """Grounded follow-up chat over the user's own data (streamed)."""
    body = await req.json()
    question = (body.get("question") or "").strip()
    history = body.get("history") or []
    data = _load()
    if not question:
        return JSONResponse({"error": "empty-question"}, status_code=400)
    if not data or not data.get("days"):
        return JSONResponse({"error": "no-data"}, status_code=400)
    analysis = forecast.analyze(data)
    stats = analytics.compute(data)

    async def gen():
        try:
            async for tok in insights.stream_chat(question, data, analysis, stats, history):
                yield tok
        except (RuntimeError, httpx.HTTPError) as e:
            yield f"\n\n[Unavailable: {e}]"

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8")


@app.post("/api/refresh")
def api_refresh(days: int = 30) -> JSONResponse:
    """Re-pull from Garmin. Runs in FastAPI's threadpool (sync def)."""
    try:
        data = dashboard.export(days=days)
    except Exception as e:  # noqa: BLE001 — report, don't crash the server
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
    n = sum(1 for d in data.get("days", []) if d.get("steps") is not None)
    return JSONResponse({"ok": True, "generated": data.get("generated"), "days_with_data": n})


def main(host: str = "127.0.0.1", port: int = 8800) -> None:
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
