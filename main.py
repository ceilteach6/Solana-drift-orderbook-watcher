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
"""

import asyncio
import os
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

        if not os.path.exists(settings.db_path):
            print(f"❌ DB not found: {settings.db_path}")
            print("   Run the watcher with STORAGE_ENABLED=true first.")
            sys.exit(1)

        store = SQLiteStore(settings.db_path)
        store.connect()
        print(store.summary())
        store.close()
        sys.exit(0)

    if "--dashboard" in sys.argv[1:]:
        from src.dashboard import run_dashboard

        sys.exit(run_dashboard(settings))

    exit_code = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        # Exit non-zero so a process supervisor (systemd Restart=on-failure,
        # Docker restart policy, k8s liveness probe) sees this as a crash and
        # restarts the watcher instead of treating it as a clean shutdown.
        exit_code = 1

    # asyncio.run(main()) has already returned, which means Watcher.start()'s
    # finally block ran feed.close()/store.close() to completion. Exit here
    # via os._exit() rather than falling off the end of __main__ (which would
    # trigger normal interpreter shutdown): CPython's concurrent.futures.thread
    # module registers a process-wide atexit hook that joins EVERY worker
    # thread any ThreadPoolExecutor in this process ever created, regardless
    # of that executor's own shutdown(wait=...) argument. If a poll tick's RPC
    # call was genuinely stuck (no socket-level timeout in the underlying
    # client), that hook would hang the whole process on exit even though
    # DriftOrderbookFeed.close() explicitly asked not to wait. We've already
    # done our own cleanup above, so skip the rest of Python's shutdown
    # machinery instead of relying on it.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)
