"""GarminGPT validation CLI.

    uv run python -m garmingpt login      # one-time: sign in to Garmin (handles MFA)
    uv run python -m garmingpt sync       # pull ~90 days into local SQLite
    uv run python -m garmingpt brief      # generate today's morning briefing
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from . import dashboard, garmin_client
from .legacy import baselines, briefing, store


def cmd_login(_: argparse.Namespace) -> int:
    email = os.environ.get("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.environ.get("GARMIN_PASSWORD") or getpass.getpass("Garmin password: ")
    garmin_client.login(email, password)
    print("Logged in. Session saved — you won't need to do this again.")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    print(f"Fetching {args.days} days from Garmin Connect…")
    data = garmin_client.fetch(days=args.days)
    total = sum(store.save(metric, rows) for metric, rows in data.items())
    print(f"Stored {total} day-records → {store.DB}")
    return 0


def cmd_brief(args: argparse.Namespace) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Set ANTHROPIC_API_KEY first:  export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        return 1
    payload = baselines.build_payload(baselines.normalize(store.load_all()))
    print(f"\n— Morning briefing · {payload['date']} —\n")
    print(briefing.generate(payload, model=args.model))
    print()
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    dashboard.export(days=args.days)
    print("Run `uv run python -m garmingpt serve` to open the ML dashboard.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from . import serve
    url = f"http://{args.host}:{args.port}"
    print(f"GarminGPT ML dashboard → {url}   (Ctrl-C to stop)")
    serve.main(host=args.host, port=args.port)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="garmingpt", description="GarminGPT — local AI health dashboard")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="Sign in to Garmin and save the session").set_defaults(fn=cmd_login)

    sp = sub.add_parser("sync", help="Pull recent days into local SQLite")
    sp.add_argument("--days", type=int, default=90)
    sp.set_defaults(fn=cmd_sync)

    bp = sub.add_parser("brief", help="Generate today's morning briefing")
    bp.add_argument("--model", default=briefing.MODEL)
    bp.set_defaults(fn=cmd_brief)

    dp = sub.add_parser("dashboard", help="Export rich data → dashboard_data.json")
    dp.add_argument("--days", type=int, default=30)
    dp.set_defaults(fn=cmd_dashboard)

    vp = sub.add_parser("serve", help="Run the local ML dashboard (data + forecasts + Ollama)")
    vp.add_argument("--host", default="127.0.0.1")
    vp.add_argument("--port", type=int, default=8800)
    vp.set_defaults(fn=cmd_serve)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
