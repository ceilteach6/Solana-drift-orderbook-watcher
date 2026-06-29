#!/usr/bin/env python3
"""
examples/quickstart.py

Minimal, network-free example: drives the synthetic feed through the full
detector stack for 20 ticks and prints any alerts.  No RPC / driftpy required.

Run from the repo root:
    python examples/quickstart.py
"""

import asyncio
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import load_settings
from src.alert import AlertDispatcher, build_alert_sinks
from src.collector.orderbook_feed import SyntheticOrderbookFeed
from src.detector import DEFAULT_DETECTORS


async def main() -> None:
    settings = load_settings()
    feed = SyntheticOrderbookFeed(settings)
    await feed.connect()

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    dispatcher = AlertDispatcher(settings, build_alert_sinks(settings))

    interval = max(settings.update_frequency_ms / 1000, 0.001)
    history_len = max(8, int(settings.flicker_window_sec / interval) + 4)
    history: dict[str, deque] = {
        m: deque(maxlen=history_len) for m in settings.markets
    }

    print(f"Running 20 synthetic ticks across {', '.join(settings.markets)}...\n")
    for _ in range(20):
        for market in settings.markets:
            snap = await feed.get_snapshot(market)
            if snap is None:
                continue
            detections = []
            for det in detectors:
                detections.extend(det.analyze(snap, history[market]))
            history[market].append(snap)
            await dispatcher.emit(detections)
        await asyncio.sleep(0.05)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
