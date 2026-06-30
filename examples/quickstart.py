#!/usr/bin/env python3
"""
examples/quickstart.py

Minimal, network-free example: drives the synthetic feed through the detector
stack for a handful of ticks and prints any alerts. No RPC / driftpy required.

Run from the repo root:
    python examples/quickstart.py
"""

import asyncio
import os
import sys

# Allow running directly from the examples/ directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import load_settings
from src.alert import AlertDispatcher, ConsoleAlert
from src.collector.orderbook_feed import SyntheticOrderbookFeed
from src.detector import DEFAULT_DETECTORS


async def main() -> None:
    settings = load_settings()
    feed = SyntheticOrderbookFeed(settings)
    await feed.connect()

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    alert = AlertDispatcher(settings, [ConsoleAlert(settings)])
    # One history list per market — detectors (spoof_pull, flicker) compare
    # the current snapshot against recent prior ones by time window only, not
    # by market, so a single shared list would mix e.g. BTC-PERP's current
    # mid-price against a SOL-PERP prior snapshot once MARKETS has more than
    # one entry.
    history: dict[str, list] = {market: [] for market in settings.markets}

    print("Running 20 synthetic ticks...\n")
    for _ in range(20):
        for market in settings.markets:
            snap = await feed.get_snapshot(market)
            if snap is None:
                continue
            market_history = history[market]
            detections = []
            for det in detectors:
                detections.extend(det.analyze(snap, market_history))
            market_history.append(snap)
            del market_history[:-16]  # bound history
            alert.emit(detections)
        await asyncio.sleep(0.05)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
