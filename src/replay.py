"""
src/replay.py

Replay / backtesting: re-runs the real detector stack (and risk aggregator)
over previously persisted L2 snapshots, so thresholds can be tuned against
actual market history without a live connection or waiting for a pattern to
reoccur.

Requires the watcher to have been run with ``STORAGE_ENABLED=true`` *and*
``PERSIST_SNAPSHOTS=true`` — the ``risk``/``detections`` tables alone don't
carry the L2 book needed to re-run detectors, only their original output.

    python main.py --replay                                # every market
    python main.py --replay --replay-market SOL-PERP
    python main.py --replay --replay-limit 20000            # most recent N ticks
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore
from src.watcher import history_length


@dataclass
class ReplayStats:
    market: str
    snapshots: int
    detections: dict[str, int] = field(default_factory=dict)
    risk_alerts: int = 0


def _snapshot_from_row(row) -> OrderbookSnapshot:
    bids = [Level(p, s) for p, s in json.loads(row["bids"])] if row["bids"] else []
    asks = [Level(p, s) for p, s in json.loads(row["asks"])] if row["asks"] else []
    return OrderbookSnapshot(market=row["market"], timestamp=row["ts"], bids=bids, asks=asks)


def _replay_market(settings, store: SQLiteStore, market: str, limit: int | None) -> ReplayStats:
    rows = store.snapshots_for_market(market, limit)
    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
    history: deque = deque(maxlen=history_length(settings))

    stats = ReplayStats(market=market, snapshots=len(rows))
    for row in rows:
        snapshot = _snapshot_from_row(row)
        detections = []
        for detector in detectors:
            for d in detector.analyze(snapshot, history):
                detections.append(d)
                stats.detections[d.detector] = stats.detections.get(d.detector, 0) + 1
        history.append(snapshot)

        if aggregator is not None and aggregator.update(market, snapshot.timestamp, detections) is not None:
            stats.risk_alerts += 1

    return stats


def run_replay(settings, *, market: str | None = None, limit: int | None = None) -> list[ReplayStats]:
    """Re-run the detector stack over persisted history.

    ``market=None`` replays every market that has persisted snapshots.
    """
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        markets = [market] if market else store.markets_with_snapshots()
        return [_replay_market(settings, store, m, limit) for m in markets]
    finally:
        store.close()


def format_replay_report(stats: list[ReplayStats]) -> str:
    lines = ["🔁 Detector replay (backtest over persisted snapshots)"]
    if not stats:
        lines.append("   No markets with persisted snapshots found "
                      "(needs STORAGE_ENABLED=true and PERSIST_SNAPSHOTS=true).")
        return "\n".join(lines)

    for s in stats:
        lines.append(f"   {s.market}  —  {s.snapshots} snapshots replayed")
        if s.detections:
            breakdown = ", ".join(f"{k}={v}" for k, v in sorted(s.detections.items()))
            lines.append(f"      detections : {breakdown}")
        else:
            lines.append("      detections : none")
        lines.append(f"      risk alerts: {s.risk_alerts}")
    return "\n".join(lines)


def replay_main(settings, argv: list[str]) -> int:
    """CLI entry point for ``python main.py --replay [...]``."""
    market = None
    limit = None
    for i, arg in enumerate(argv):
        if arg == "--replay-market" and i + 1 < len(argv):
            market = argv[i + 1]
        elif arg == "--replay-limit" and i + 1 < len(argv):
            try:
                limit = int(argv[i + 1])
            except ValueError:
                print(f"❌ --replay-limit must be an integer (got {argv[i + 1]!r})")
                return 1

    if not settings.storage_enabled:
        print("❌ STORAGE_ENABLED is false — nothing has been persisted to replay.")
        return 1

    stats = run_replay(settings, market=market, limit=limit)
    print(format_replay_report(stats))
    return 0 if stats else 1
