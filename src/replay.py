"""
src/replay.py

Replay / backtesting: re-runs persisted L2 snapshots (``PERSIST_SNAPSHOTS=true``)
back through the live detector stack and risk aggregator, so detector and risk
thresholds can be tuned offline against captured market data instead of
guessing from a live run.

Read-only over the existing ``snapshots`` table. It never writes into
``detections``/``risk`` — a replay can't corrupt a live run's history, and can
be re-run any number of times with different settings/thresholds.

    python main.py --replay                       # every market with snapshots
    python main.py --replay --replay-market SOL-PERP
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.detector.base import Detection
from src.risk import RiskAggregator
from src.storage import SQLiteStore

# Bound how much history each detector sees during replay (mirrors the live
# watcher's flicker-window-derived deque, but a fixed cap is simpler here and
# comfortably covers every detector's lookback window).
_MAX_HISTORY = 500


@dataclass
class ReplaySummary:
    market: str
    snapshots: int
    detections: int
    by_detector: dict[str, int] = field(default_factory=dict)
    risk_alerts: int = 0
    max_risk: float = 0.0


def _row_to_snapshot(row) -> OrderbookSnapshot:
    bids = [Level(p, s) for p, s in json.loads(row["bids"] or "[]")]
    asks = [Level(p, s) for p, s in json.loads(row["asks"] or "[]")]
    return OrderbookSnapshot(market=row["market"], timestamp=row["ts"], bids=bids, asks=asks)


def replay_market(store: SQLiteStore, settings, market: str, *, limit: int = 100_000) -> ReplaySummary:
    """Replay every persisted snapshot for ``market`` through the live stack."""
    rows = store.snapshots_for_replay(market, limit=limit)
    if not rows:
        return ReplaySummary(market=market, snapshots=0, detections=0)

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None

    history: list[OrderbookSnapshot] = []
    by_detector: dict[str, int] = {}
    total_detections = 0
    risk_alerts = 0
    max_risk = 0.0

    for row in rows:
        snapshot = _row_to_snapshot(row)
        detections: list[Detection] = []
        for detector in detectors:
            try:
                detections.extend(detector.analyze(snapshot, history))
            except Exception:
                # A single malformed/edge-case snapshot shouldn't abort the
                # whole replay — same fault isolation as the live watcher.
                continue

        history.append(snapshot)
        if len(history) > _MAX_HISTORY:
            del history[: len(history) - _MAX_HISTORY]

        for d in detections:
            by_detector[d.detector] = by_detector.get(d.detector, 0) + 1
        total_detections += len(detections)

        if aggregator is not None:
            risk = aggregator.update(market, snapshot.timestamp, detections)
            if risk is not None:
                risk_alerts += 1
            max_risk = max(max_risk, aggregator.score(market))

    return ReplaySummary(
        market=market,
        snapshots=len(rows),
        detections=total_detections,
        by_detector=by_detector,
        risk_alerts=risk_alerts,
        max_risk=round(max_risk, 4),
    )


def format_summary(summary: ReplaySummary) -> str:
    lines = [f"🔁 Replay — {summary.market}", f"   Snapshots : {summary.snapshots}"]
    if summary.snapshots == 0:
        lines.append("   (no persisted snapshots for this market)")
        return "\n".join(lines)
    lines.append(f"   Detections: {summary.detections}")
    for name, count in sorted(summary.by_detector.items()):
        lines.append(f"     {name.ljust(14)}: {count}")
    lines.append(f"   Risk      : {summary.risk_alerts} alert(s), peak score {summary.max_risk:.2f}")
    return "\n".join(lines)


def replay_main(settings, market: str | None = None) -> int:
    """Entry point for ``python main.py --replay [--replay-market MKT]``."""
    import os

    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        print("   Run the watcher with STORAGE_ENABLED=true PERSIST_SNAPSHOTS=true first.")
        return 1

    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        markets = [market] if market else store.markets_with_snapshots()
        if not markets:
            print("❌ No persisted snapshots found.")
            print("   Run the watcher with STORAGE_ENABLED=true PERSIST_SNAPSHOTS=true first.")
            return 1
        for m in markets:
            print(format_summary(replay_market(store, settings, m)))
        return 0
    finally:
        store.close()
