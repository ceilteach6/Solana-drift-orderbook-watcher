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
import signal
import sys

from config.settings import settings
from src.watcher import Watcher


async def main():
    watcher = Watcher(settings)
    run_task = asyncio.ensure_future(watcher.start())

    # SIGTERM is how Docker/systemd/Kubernetes ask a process to stop; without
    # a handler it kills the process immediately, skipping the watcher's
    # `finally` cleanup (closing the feed and the storage connection). Both
    # signals now request a graceful shutdown the same way Ctrl+C does.
    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _request_stop(sig):
        if not stop.done():
            stop.set_result(sig)

    registered = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop, sig)
            registered.append(sig)
        except (NotImplementedError, RuntimeError):
            pass  # not supported on this platform; KeyboardInterrupt still works for SIGINT

    try:
        done, _ = await asyncio.wait(
            {run_task, stop}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop in done and run_task not in done:
            print(f"\n⏹️  Received {signal.Signals(stop.result()).name} — shutting down…")
            run_task.cancel()
            try:
                await run_task
            except asyncio.CancelledError:
                pass
        elif run_task in done:
            run_task.result()  # surface any exception raised by the watcher
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)


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
