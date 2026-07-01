"""
src/replay.py

Replay / backtesting: re-runs the detector + risk-aggregator pipeline over
snapshots the watcher previously persisted (``STORAGE_ENABLED=true`` +
``PERSIST_SNAPSHOTS=true``), so thresholds can be tuned against captured
history without touching the live feed.

    python main.py --replay SOL-PERP                # as fast as possible
    python main.py --replay SOL-PERP --speed 10      # 10x realtime pacing

This builds a fresh detector stack, risk aggregator, and alert dispatcher —
independent from any live watcher — so replaying never touches the live
in-memory state or double-counts against the stored detections table.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field

from src.alert import AlertDispatcher, build_alert_sinks
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore
from src.watcher import history_length


@dataclass
class ReplayStats:
    ticks: int = 0
    detections: int = 0
    alerts: int = 0
    by_detector: dict[str, int] = field(default_factory=dict)


def _snapshot_from_row(market: str, row) -> OrderbookSnapshot:
    bids = [Level(p, s) for p, s in json.loads(row["bids"] or "[]")]
    asks = [Level(p, s) for p, s in json.loads(row["asks"] or "[]")]
    return OrderbookSnapshot(market=market, timestamp=row["ts"], bids=bids, asks=asks)


async def replay(settings, market: str, speed: float = 0.0) -> ReplayStats:
    """Replay every persisted snapshot for ``market`` through a fresh pipeline.

    ``speed`` is a realtime pacing multiplier (2.0 = twice as fast as the
    original capture); 0 (the default) replays with no pacing at all.

    Raises ``ValueError`` if no snapshots were persisted for ``market``.
    """
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        rows = store.snapshot_rows(market)
    finally:
        store.close()

    if not rows:
        raise ValueError(
            f"No persisted snapshots for {market!r} in {settings.db_path}. "
            "Run the watcher with STORAGE_ENABLED=true and PERSIST_SNAPSHOTS=true "
            "for a while first."
        )

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
    alert = AlertDispatcher(settings, build_alert_sinks(settings))

    history: deque = deque(maxlen=history_length(settings))
    stats = ReplayStats()
    prev_ts = None

    for row in rows:
        snapshot = _snapshot_from_row(market, row)
        if speed > 0 and prev_ts is not None:
            gap = (snapshot.timestamp - prev_ts) / speed
            if gap > 0:
                await asyncio.sleep(gap)
        prev_ts = snapshot.timestamp

        detections = []
        for detector in detectors:
            detections.extend(detector.analyze(snapshot, history))
        history.append(snapshot)

        stats.ticks += 1
        stats.detections += len(detections)
        for d in detections:
            stats.by_detector[d.detector] = stats.by_detector.get(d.detector, 0) + 1

        if aggregator is not None:
            risk = aggregator.update(market, snapshot.timestamp, detections)
            if risk is not None:
                stats.alerts += alert.emit([risk])
        elif detections:
            stats.alerts += alert.emit(detections)

    return stats


def format_report(market: str, stats: ReplayStats) -> str:
    lines = [f"⏪ Replay report — {market}", f"   Ticks      : {stats.ticks}"]
    lines.append(f"   Detections : {stats.detections}")
    for name, count in sorted(stats.by_detector.items()):
        lines.append(f"     {name.ljust(14)}: {count}")
    lines.append(f"   Alerts     : {stats.alerts}")
    return "\n".join(lines)


def replay_main(settings, market: str, speed: float) -> int:
    """Run the replay, print a report, and return a process exit code."""
    try:
        stats = asyncio.run(replay(settings, market, speed))
    except ValueError as exc:
        print(f"❌ {exc}")
        return 1
    print(format_report(market, stats))
    return 0
