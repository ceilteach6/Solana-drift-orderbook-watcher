"""
src/replay.py

Replay / backtesting: re-run the current detector stack (and risk aggregator,
if enabled) over L2 snapshots already persisted in SQLite, instead of a live
feed. This is how you tune detector thresholds against real recorded history
— change a threshold in ``.env``, replay the same recorded window, and see
which detections would have fired differently, without waiting for new data.

Requires a prior run with ``STORAGE_ENABLED=true`` *and*
``PERSIST_SNAPSHOTS=true`` (replay works off full L2 books, not just the
detections/risk tables, since detectors need the raw orderbook).

    python main.py --replay SOL-PERP [--limit 5000]
"""

from __future__ import annotations

from collections import deque

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore


def _snapshot_from_row(market: str, row: dict) -> OrderbookSnapshot:
    return OrderbookSnapshot(
        market=market,
        timestamp=row["ts"],
        bids=[Level(p, s) for p, s in row["bids"]],
        asks=[Level(p, s) for p, s in row["asks"]],
    )


def replay(settings, market: str, limit: int = 5000) -> list[tuple[float, list]]:
    """Re-run the detector stack over persisted history for ``market``.

    Returns a chronological list of ``(timestamp, detections)`` — mirrors
    what :meth:`src.watcher.Watcher._tick` would have emitted per tick: a
    single consolidated risk `Detection` when `RISK_AGGREGATION` is on,
    otherwise the raw per-detector list.
    """
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        rows = store.read_snapshots(market, limit)
    finally:
        store.close()
    if not rows:
        return []

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None

    interval = max(settings.update_frequency_ms / 1000, 0.001)
    history_len = max(8, int(settings.flicker_window_sec / interval) + 4)
    history: deque = deque(maxlen=history_len)

    results: list[tuple[float, list]] = []
    for row in rows:
        snapshot = _snapshot_from_row(market, row)
        detections = []
        for detector in detectors:
            detections.extend(detector.analyze(snapshot, history))
        history.append(snapshot)

        if aggregator is not None:
            risk = aggregator.update(market, snapshot.timestamp, detections)
            results.append((snapshot.timestamp, [risk] if risk else []))
        else:
            results.append((snapshot.timestamp, detections))
    return results


def replay_main(settings, market: str, limit: int = 5000) -> int:
    """CLI entry: replay and print a report. Returns a process exit code."""
    print(f"⏪ Replaying {market} (up to {limit} persisted snapshots, "
          f"DB: {settings.db_path})")
    results = replay(settings, market, limit)
    if not results:
        print("   No persisted snapshots found for this market — "
              "was PERSIST_SNAPSHOTS=true during the recording run?")
        return 1

    total = sum(len(d) for _, d in results)
    print(f"   {len(results)} snapshots replayed, {total} detection(s) fired.\n")
    for ts, detections in results:
        for d in detections:
            print(f"   t={ts:.0f}  {d.detector:14s} score={d.score:.2f}  {d.message}")
    return 0
