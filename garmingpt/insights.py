"""Local-Ollama insight layer: structured predictions + grounded chat.

The model only ever sees the user's OWN summarised metrics, forecasts, recovery
score, training load and driver correlations. It must not invent numbers or give
medical diagnosis. Two entry points:
  - structured_insight(): one constrained-JSON forecast (state / predictions /
    risks / recommendations / watch) rendered as a rich card.
  - stream_chat(): free-form follow-up questions, answered from the same context.
If Ollama is down or has no usable model, callers get an explicit error.
"""
from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

OLLAMA = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
if not OLLAMA.startswith("http"):
    OLLAMA = "http://" + OLLAMA
# GARMINGPT_MODEL (set by the one-click installer for tiny machines) wins.
# Otherwise pick the best INSTALLED model, quality-first — so a beefy box that has
# the 14b uses it, while a fresh tiny-machine install (which only has the small
# model the installer pulled) falls through to it automatically.
PREFERRED = [m for m in [os.getenv("GARMINGPT_MODEL")] if m] + [
    "qwen2.5:14b-instruct", "llama3.2:3b", "qwen2.5:3b", "qwen2.5:3b-instruct",
    "granite4.1:3b", "llama3.2:1b",
]

_GROUND = (
    "You read ONE person's own Garmin wearable data plus simple statistics derived "
    "from it (trend forecasts, a composite recovery score, acute:chronic training "
    "load, and driver correlations). Rules: cite only the numbers you are given; "
    "never invent metrics or readings; the history is short and gappy, so stay "
    "honest about uncertainty; connect metrics physiologically (rising resting HR + "
    "falling HRV = accumulating fatigue; strong sleep aids recovery; ACWR > 1.5 = "
    "load spike). This is NOT medical advice or diagnosis."
)

STRUCT_SYSTEM = (
    _GROUND + " Return a structured near-term (next ~7 days) forecast as JSON "
    "matching the schema. Be specific and quantitative; every claim must trace to "
    "the data. Recommendations must be concrete and personal to these numbers."
)

CHAT_SYSTEM = (
    _GROUND + " Answer the user's question about their data directly and concisely, "
    "grounded in the CONTEXT below. If the data can't answer it, say so. No preamble."
)

INSIGHT_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "3-5 word headline of current trajectory"},
                "tone": {"type": "string", "enum": ["positive", "neutral", "caution", "risk"]},
            },
            "required": ["label", "tone"],
        },
        "summary": {"type": "string", "description": "2-3 sentence read of where they're heading"},
        "predictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "direction": {"type": "string", "enum": ["improving", "declining", "stable"]},
                    "detail": {"type": "string", "description": "expected value/range with the date, from the forecasts"},
                },
                "required": ["metric", "direction", "detail"],
            },
        },
        "risks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "detail": {"type": "string"},
                },
                "required": ["label", "severity", "detail"],
            },
        },
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["action", "why"],
            },
        },
        "watch": {"type": "string", "description": "the single most important thing to monitor"},
    },
    "required": ["state", "summary", "predictions", "risks", "recommendations", "watch"],
}


# ---------------- model discovery ----------------

async def available_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{OLLAMA}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:  # noqa: BLE001
        return []


async def pick_model(requested: str | None = None) -> str | None:
    models = await available_models()
    if not models:
        return None
    if requested and requested in models:
        return requested
    for p in PREFERRED:
        if p in models:
            return p
    for m in models:  # any generative model beats an embedding model
        if "embed" not in m and "bge" not in m:
            return m
    return models[0]


# ---------------- context assembly ----------------

def _fmt_forecast(m: dict[str, Any]) -> str:
    end = m["forecast"][-1] if m["forecast"] else None
    tail = f", 7-day ~{end['mean']} ({end['lo']}-{end['hi']})" if end else ""
    flag = "  [ANOMALY]" if m["anomaly"] else ""
    return (f"- {m['label']}: now-week avg {m['recent_mean']}{m['unit']} vs prior {m['prior_mean']}, "
            f"{m['trend']} ({m['weekly_change']:+}/wk), {m['n']}d {m['confidence']}-conf{tail}{flag}")


def build_context(data: dict, analysis: dict, analytics: dict) -> str:
    sig = analysis["signals"]
    L = [f"Span: {sig['span_days']} days, {sig['days_with_data']} with readings."]

    rec = analytics.get("recovery")
    if rec:
        comp = ", ".join(f"{c['label']} {c['latest']} (z{c['z']:+})" for c in rec["components"])
        L.append(f"\nRecovery score: {rec['score']}/100 ({rec['band']}). Components: {comp}."
                 + (f" Weakest: {rec['drag']}." if rec.get("drag") else ""))

    load = analytics.get("training_load")
    if load and load.get("acwr") is not None:
        L.append(f"Training load: acute {load['acute']} / chronic {load['chronic']} {load['unit']}, "
                 f"ACWR {load['acwr']} ({load['label']}).")

    drv = analytics.get("drivers") or {}
    for t in drv.values():
        top = "; ".join(f"{d['label']} r={d['r']} ({d['strength']}, n={d['n']})" for d in t["drivers"][:3])
        L.append(f"What moves {t['label']}: {top}.")

    L.append("\nMetric trends & forecasts:")
    L += [_fmt_forecast(m) for m in analysis["metrics"].values()]

    acts = data.get("activities", [])[:6]
    if acts:
        L.append("\nRecent activities:")
        for a in acts:
            dist = f"{a['distance_km']}km " if a.get("distance_km") else ""
            hr = f"avgHR {a['avg_hr']}" if a.get("avg_hr") else ""
            L.append(f"- {a.get('date')}: {a.get('type', '').replace('_', ' ')} {dist}{hr}".rstrip())
    return "\n".join(L)


# ---------------- structured insight ----------------

async def structured_insight(data: dict, analysis: dict, analytics: dict, model: str | None = None) -> dict:
    chosen = await pick_model(model)
    if not chosen:
        raise RuntimeError("Ollama not reachable or no usable model on :11434")
    payload = {
        "model": chosen,
        "messages": [
            {"role": "system", "content": STRUCT_SYSTEM},
            {"role": "user", "content": build_context(data, analysis, analytics) + "\n\nProduce the structured forecast now."},
        ],
        "stream": False,
        "format": INSIGHT_SCHEMA,
        "options": {"temperature": 0.3, "num_ctx": 8192},
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as c:
        r = await c.post(f"{OLLAMA}/api/chat", json=payload)
        r.raise_for_status()
        content = (r.json().get("message") or {}).get("content", "").strip()
    out = json.loads(content)
    out["_model"] = chosen
    return out


# ---------------- chat ----------------

async def stream_chat(question: str, data: dict, analysis: dict, analytics: dict,
                      history: list[dict] | None = None, model: str | None = None) -> AsyncIterator[str]:
    chosen = await pick_model(model)
    if not chosen:
        raise RuntimeError("Ollama not reachable or no usable model on :11434")
    msgs = [{"role": "system", "content": CHAT_SYSTEM + "\n\nCONTEXT:\n" + build_context(data, analysis, analytics)}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:2000]})
    msgs.append({"role": "user", "content": question[:1000]})

    payload = {"model": chosen, "messages": msgs, "stream": True, "options": {"temperature": 0.4, "num_ctx": 8192}}
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as c:
        async with c.stream("POST", f"{OLLAMA}/api/chat", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tok = (obj.get("message") or {}).get("content", "")
                if tok:
                    yield tok
                if obj.get("done"):
                    break
