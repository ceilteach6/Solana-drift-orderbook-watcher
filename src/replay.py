"""
src/replay.py

Offline replay / backtesting: re-runs previously persisted L2 snapshots
through the *real* detector + risk-aggregator stack, network-free. This is
how you tune thresholds against a recorded session instead of guessing
against the live market.

Prerequisite: the watcher must have been run with ``STORAGE_ENABLED=true``
and ``PERSIST_SNAPSHOTS=true`` so full L2 books (not just detections/risk)
were written to the DB.

Run:
    python main.py --replay SOL-PERP
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore


@dataclass
class ReplayResult:
    market: str
    snapshots: int
    detections: dict[str, int] = field(default_factory=dict)
    risk_alerts: int = 0


def _history_len(settings) -> int:
    # Mirrors src/watcher.py's sizing so flicker/spoof_pull see the same
    # lookback window during replay as they would live.
    interval = max(settings.update_frequency_ms / 1000, 0.001)
    return max(8, int(settings.flicker_window_sec / interval) + 4)


def replay_market(settings, market: str, limit: int = 100_000) -> ReplayResult:
    """Replay persisted snapshots for ``market`` through the detector stack."""
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        rows = list(store.iter_snapshots(market, limit))
    finally:
        store.close()

    result = ReplayResult(market=market, snapshots=len(rows))
    if not rows:
        return result

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
    history = deque(maxlen=_history_len(settings))

    for ts, bids, asks in rows:
        snapshot = OrderbookSnapshot(
            market=market,
            timestamp=ts,
            bids=[Level(p, s) for p, s in bids],
            asks=[Level(p, s) for p, s in asks],
        )
        detections = []
        for detector in detectors:
            detections.extend(detector.analyze(snapshot, history))
        history.append(snapshot)

        for d in detections:
            result.detections[d.detector] = result.detections.get(d.detector, 0) + 1

        if aggregator is not None and aggregator.update(market, ts, detections) is not None:
            result.risk_alerts += 1

    return result


def format_replay_report(result: ReplayResult) -> str:
    lines = [f"⏪ Replay — {result.market}", f"   Snapshots : {result.snapshots}"]
    if result.snapshots == 0:
        lines.append("   No persisted snapshots for this market.")
        lines.append(
            "   Run the watcher with STORAGE_ENABLED=true and "
            "PERSIST_SNAPSHOTS=true first, then replay."
        )
        return "\n".join(lines)

    if result.detections:
        lines.append("   Detections:")
        width = max(len(name) for name in result.detections)
        for name, n in sorted(result.detections.items()):
            lines.append(f"     {name.ljust(width)} : {n}")
    else:
        lines.append("   Detections : none")
    lines.append(f"   Risk alerts: {result.risk_alerts}")
    return "\n".join(lines)


def replay_main(settings, market: str) -> int:
    """Run a replay, print the report, and return a process exit code."""
    result = replay_market(settings, market)
    print(format_replay_report(result))
    return 0 if result.snapshots > 0 else 1
