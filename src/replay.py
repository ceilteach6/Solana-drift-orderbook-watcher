"""
src/replay.py

Replay: re-runs a market's persisted L2 orderbook history through the live
detector stack and (optionally) the risk aggregator, tick-by-tick, exactly as
the live watcher would have processed it. Useful for tuning detector
thresholds against real historical data without waiting for a pattern to
recur live.

Requires ``PERSIST_SNAPSHOTS=true`` to have been set while the watcher was
recording — otherwise the ``snapshots`` table is empty and there is nothing
to replay.

    python main.py --replay SOL-PERP
"""

from __future__ import annotations

import os
from collections import deque

from src.alert import AlertDispatcher, build_alert_sinks
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore


def replay_market(settings, market: str, start_ts=None, end_ts=None) -> int:
    """Replay one market's persisted snapshots through the detector stack.

    Emits alerts through the normal dispatcher/sinks as it goes, then prints
    a per-detector count summary. Returns the number of snapshots replayed.
    """
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
        alert = AlertDispatcher(settings, build_alert_sinks(settings))
        aggregator = RiskAggregator(settings) if settings.risk_aggregation else None

        # Same history window sizing as Watcher, so replay reproduces exactly
        # what the live run would have seen (same flicker/spoof lookback).
        interval = max(settings.update_frequency_ms / 1000, 0.001)
        history_len = max(8, int(settings.flicker_window_sec / interval) + 4)
        history: deque = deque(maxlen=history_len)

        detection_counts: dict[str, int] = {}
        snapshot_count = 0
        for snapshot in store.replay_snapshots(market, start_ts, end_ts):
            snapshot_count += 1
            detections = []
            for detector in detectors:
                detections.extend(detector.analyze(snapshot, history))
            history.append(snapshot)

            for d in detections:
                detection_counts[d.detector] = detection_counts.get(d.detector, 0) + 1

            if aggregator is not None:
                risk = aggregator.update(market, snapshot.timestamp, detections)
                if risk is not None:
                    alert.emit([risk])
            elif detections:
                alert.emit(detections)

        _print_summary(market, snapshot_count, detection_counts, aggregator)
        return snapshot_count
    finally:
        store.close()


def _print_summary(market, snapshot_count, detection_counts, aggregator) -> None:
    print(f"\n📼 Replay of {market}: {snapshot_count} snapshots")
    if not detection_counts:
        print("   (no detections)")
    else:
        for name, count in sorted(detection_counts.items()):
            print(f"   {name.ljust(14)}: {count}")
    if aggregator is not None:
        print(f"   final risk    : {aggregator.score(market):.3f}")


def replay_main(settings, market: str) -> int:
    """CLI entry point for ``python main.py --replay MARKET``."""
    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        return 1
    snapshot_count = replay_market(settings, market)
    if snapshot_count == 0:
        print(
            "   No snapshots found for that market. Replay needs "
            "PERSIST_SNAPSHOTS=true while the watcher was recording."
        )
        return 1
    return 0
