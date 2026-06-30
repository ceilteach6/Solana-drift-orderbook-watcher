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

import argparse
import asyncio
import signal
import sys
import traceback

try:
    # config/settings.py parses every env var eagerly at import time, so a
    # malformed value (e.g. SPOOF_WALL_RATIO=5x) raises ConfigError here —
    # before argparse even runs, for every entry point including --selftest.
    # Caught as ValueError (its base class) since the import itself is what
    # would bind the ConfigError name otherwise.
    from config.settings import settings
except ValueError as exc:
    print(f"\n❌ Configuration error: {exc}")
    print("   Check your .env against config.example.env.")
    sys.exit(1)

from src.watcher import Watcher


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="main.py", description="Drift Orderbook Watcher & Bot Detector"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--selftest", action="store_true",
        help="run the algorithmic self-test (no network) and exit",
    )
    mode.add_argument(
        "--dbstats", action="store_true",
        help="print stored time-series row counts + recent detections and exit",
    )
    mode.add_argument(
        "--dashboard", action="store_true",
        help="serve the charting dashboard (reads the stored time-series)",
    )
    # Unrecognized flags are a hard error here (argparse default), instead of
    # being silently ignored and falling through to launching the live
    # watcher against real RPC/markets.
    return parser.parse_args(argv)


async def main() -> None:
    watcher = Watcher(settings)
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()

    # SIGINT already raises KeyboardInterrupt via Python's default handler,
    # caught below. SIGTERM (what Docker/Kubernetes send to stop a
    # container) has no such default — without this it kills the process
    # immediately and skips the feed/store cleanup in Watcher.start()'s
    # finally block.
    def _on_sigterm() -> None:
        task.cancel()

    try:
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)
    except NotImplementedError:
        pass  # e.g. Windows, where add_signal_handler is unsupported

    try:
        await watcher.start()
    except asyncio.CancelledError:
        print("\n⏹️  Terminated (SIGTERM)")


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])

    if args.selftest:
        from src.selftest import selftest_main

        sys.exit(selftest_main(settings))

    if args.dbstats:
        from src.storage import SQLiteStore

        store = SQLiteStore(settings.db_path)
        exit_code = 0
        try:
            store.connect()
            print(store.summary())
        except Exception as e:
            print(f"\n❌ Error reading storage: {e}")
            exit_code = 1
        finally:
            store.close()
        sys.exit(exit_code)

    if args.dashboard:
        from src.dashboard import run_dashboard

        sys.exit(run_dashboard(settings))

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⏹️  Interrupted (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)
