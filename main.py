#!/usr/bin/env python3
"""
main.py

Drift Orderbook Watcher & Bot Detector — entry point.

Read-only: watches the Drift DLOB orderbook in real time and detects
bot-like patterns. It does not trade and does not write to the chain.

Run:
  cp config.example.env .env   # set RPC_URL
  python main.py

Self-test (no network; verifies every detector fires on a known pattern):
  python main.py --selftest

Inspect the stored time-series (row counts + recent detections):
  python main.py --dbstats

Serve the charting dashboard (reads the stored time-series):
  python main.py --dashboard

Replay persisted snapshots through the detector stack (needs
PERSIST_SNAPSHOTS=true while capturing):
  python main.py --replay SOL-PERP
  python main.py --replay SOL-PERP --speed 10   # 10x realtime pacing
"""

import asyncio
import sys

from config.settings import settings
from src.watcher import Watcher


async def main():
    watcher = Watcher(settings)
    await watcher.start()


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        from src.selftest import selftest_main

        sys.exit(selftest_main(settings))

    if "--dbstats" in sys.argv[1:]:
        from src.storage import SQLiteStore

        store = SQLiteStore(settings.db_path)
        store.connect()
        print(store.summary())
        store.close()
        sys.exit(0)

    if "--dashboard" in sys.argv[1:]:
        from src.dashboard import run_dashboard

        sys.exit(run_dashboard(settings))

    if "--replay" in sys.argv[1:]:
        from src.replay import replay_main

        args = sys.argv[1:]
        idx = args.index("--replay")
        market = args[idx + 1] if idx + 1 < len(args) and not args[idx + 1].startswith("--") else None
        if not market:
            print("Usage: python main.py --replay <MARKET> [--speed N]")
            sys.exit(1)

        speed = 0.0
        if "--speed" in args:
            sidx = args.index("--speed")
            if sidx + 1 >= len(args):
                print("❌ --speed requires a numeric value")
                sys.exit(1)
            try:
                speed = float(args[sidx + 1])
            except ValueError:
                print(f"❌ --speed value must be numeric, got {args[sidx + 1]!r}")
                sys.exit(1)

        sys.exit(replay_main(settings, market, speed))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
