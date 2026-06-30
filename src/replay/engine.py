"""
src/replay/engine.py

Replay / backtesting: re-run stored L2 snapshots through a fresh detector stack
and risk aggregator using the *current* settings. This lets you tune thresholds
offline — change `.env`, replay yesterday's data, compare the outcome — without
a live market.

It is venue-agnostic: any collector that stored snapshots (Drift today, other
Solana venues later) replays the same way.

Requires snapshots captured with ``PERSIST_SNAPSHOTS=true``.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator


@dataclass
class ReplayResult:
    market: str
    snapshots: int = 0
    detections_by_detector: dict[str, int] = field(default_factory=dict)
    risk_alerts: int = 0
    max_risk: float = 0.0
    start_ts: float | None = None
    end_ts: float | None = None


def _history_len(settings) -> int:
    interval = max(settings.update_frequency_ms / 1000, 0.001)
    return max(8, int(settings.flicker_window_sec / interval) + 4)


def replay_snapshots(settings, market: str, snapshots) -> ReplayResult:
    """Run an ordered snapshot sequence through the stack; return a summary."""
    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
    history: deque = deque(maxlen=_history_len(settings))

    counts: dict[str, int] = defaultdict(int)
    result = ReplayResult(market=market)

    for snapshot in snapshots:
        detections = []
        for detector in detectors:
            detections.extend(detector.analyze(snapshot, history))
        history.append(snapshot)

        for d in detections:
            counts[d.detector] += 1
        if aggregator is not None:
            if aggregator.update(market, snapshot.timestamp, detections) is not None:
                result.risk_alerts += 1
            result.max_risk = max(result.max_risk, aggregator.score(market))

        result.snapshots += 1
        if result.start_ts is None:
            result.start_ts = snapshot.timestamp
        result.end_ts = snapshot.timestamp

    result.detections_by_detector = dict(counts)
    return result


def _row_to_snapshot(market: str, row) -> OrderbookSnapshot:
    bids = [Level(p, s) for p, s in json.loads(row["bids"] or "[]")]
    asks = [Level(p, s) for p, s in json.loads(row["asks"] or "[]")]
    return OrderbookSnapshot(market=market, timestamp=row["ts"], bids=bids, asks=asks)


def run_replay(settings) -> int:
    """Replay every market's stored snapshots; print a report. Returns exit code."""
    import os

    from src.storage import SQLiteStore

    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        return 1

    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        markets = store.snapshot_markets()
        if not markets:
            print("❌ No stored snapshots to replay.")
            print("   Run the watcher with STORAGE_ENABLED=true PERSIST_SNAPSHOTS=true.")
            return 1
        results = []
        for market in markets:
            rows = store.read_snapshots_raw(market)
            snaps = [_row_to_snapshot(market, r) for r in rows]
            results.append(replay_snapshots(settings, market, snaps))
    finally:
        store.close()

    print(format_replay_report(results))
    return 0


def format_replay_report(results) -> str:
    lines = ["⏪ Replay / backtest"]
    for r in results:
        span = (r.end_ts - r.start_ts) if (r.start_ts and r.end_ts) else 0.0
        lines.append(
            f"   {r.market}: {r.snapshots} snapshots over {span:.0f}s "
            f"→ {r.risk_alerts} risk alerts (max risk {r.max_risk:.2f})"
        )
        if r.detections_by_detector:
            for name, n in sorted(r.detections_by_detector.items()):
                lines.append(f"       {name.ljust(14)} {n}")
        else:
            lines.append("       (no detections)")
    return "\n".join(lines)
