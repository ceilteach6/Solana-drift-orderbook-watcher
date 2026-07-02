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
    # One history buffer per market — a single shared list would let the
    # flicker/spoof-pull detectors compare price levels across unrelated
    # markets (e.g. BTC-PERP snapshots polluting SOL-PERP's window),
    # producing false positives or masking real patterns. src/watcher.py
    # keeps its history per-market for the same reason; this mirrors it.
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
            market_history[:] = market_history[-16:]  # bound history
            alert.emit(detections)
        await asyncio.sleep(0.05)

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
