"""Turn baseline deltas into a morning briefing via Claude.

The insight layer is the product. If this output reads like a generic
"get more sleep" fortune cookie, there is no product — that's what we're testing.
"""
from __future__ import annotations

import json

import anthropic

MODEL = "claude-opus-4-8"

SYSTEM = """You are a Garmin health briefing assistant for an endurance-minded user.
You receive one day's wearable metrics alongside that user's own 28-day baseline and a 7-day trend.

Write a short morning briefing. Rules:
- Lead with a one-line headline: how the body looks today, in plain language.
- Then call out ONLY the metrics that deviate meaningfully from the user's own baseline (use the deltas). Cite the actual numbers. Ignore metrics that are normal.
- Connect the dots: e.g. low HRV + elevated resting HR + poor sleep together suggest incomplete recovery — reason across metrics, don't list them in isolation.
- End with "Today's read:" — recovery / easy / moderate / hard — with one sentence of why, grounded in the data above.
- Be specific and concise. No filler, no hedging, no generic advice ("stay hydrated", "listen to your body"). If the data is unremarkable, say so plainly.
- You are not a doctor. Do not diagnose. If a metric looks alarming (e.g. resting HR far above baseline for days), suggest they consider rest or a professional — don't speculate on causes.
Plain text only — this prints in a terminal."""

PROMPT = """Here is today's Garmin data with the user's personal baselines. \
Values may be null where the watch didn't record them — work with what's present.

"""


def generate(payload: dict, model: str = MODEL) -> str:
    """Call Claude and return the briefing text."""
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model=model,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=SYSTEM,
        messages=[{"role": "user", "content": PROMPT + json.dumps(payload, indent=2, default=str)}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
