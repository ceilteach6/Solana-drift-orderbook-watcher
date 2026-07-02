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
import contextlib
import signal
import sys

from config.settings import settings
from src.watcher import Watcher


async def main():
    watcher = Watcher(settings)
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    # SIGTERM is what Docker/k8s/systemd send on a normal stop/restart;
    # SIGINT is Ctrl+C. Python's default disposition for SIGTERM is immediate
    # termination (unlike SIGINT, which raises a catchable KeyboardInterrupt),
    # so without this handler Watcher.start()'s `finally` — which closes the
    # feed's RPC/websocket connections and the SQLite store — never runs on
    # `docker stop` / pod deletion. Routing both signals through the same
    # cancellation path makes shutdown behave identically either way.
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # e.g. Windows, where add_signal_handler isn't supported

    watcher_task = asyncio.create_task(watcher.start())
    stop_task = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait(
            {watcher_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if watcher_task in done:
            watcher_task.result()  # propagate a real failure, if any
        else:
            print("\n⏹️  Shutting down (signal received)…")
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
    finally:
        stop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stop_task


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
        sys.exit(1)
