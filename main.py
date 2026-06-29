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

Replay stored snapshots through the detectors with the current settings:
  python main.py --replay
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
        from src.replay import run_replay

        sys.exit(run_replay(settings))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
