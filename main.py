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

Replay a recorded session through the live detector stack (requires
STORAGE_ENABLED=true + PERSIST_SNAPSHOTS=true while collecting):
  python main.py --replay SOL-PERP
"""

import asyncio
import sys

try:
    from config.settings import settings
except ValueError as exc:
    # Settings.__post_init__ raises on a misconfigured .env (bad threshold,
    # zero polling interval, etc.) — surface just the message, not a
    # traceback, since this is a config problem, not a code crash.
    print(f"❌ Configuration error: {exc}")
    print("\n   Fix the values in your .env (see config.example.env) and retry.")
    sys.exit(1)

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

        idx = sys.argv.index("--replay")
        rest = sys.argv[idx + 1:]
        market = rest[0] if rest and not rest[0].startswith("--") else None
        if market is None:
            print("Usage: python main.py --replay <MARKET>")
            print(f"   Known markets: {', '.join(settings.markets)}")
            sys.exit(1)
        sys.exit(replay_main(settings, market))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
