"""
src/replay/runner.py

Offline replay / threshold-tuning harness: re-runs the live detector stack +
risk aggregator over previously persisted L2 snapshots (``PERSIST_SNAPSHOTS=
true`` on the recording run) so a threshold change in ``.env`` can be
evaluated against real history, without a live connection or waiting for new
data to arrive.

Read-only: this never writes back to the database. Each run builds fresh
detector/aggregator instances from the *current* settings, independent of
whatever a live watcher process is doing.

    python main.py --replay
    python main.py --replay --replay-market=SOL-PERP
    python main.py --replay --replay-limit=20000   # most recent N snapshots
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore

logger = logging.getLogger(__name__)


def _snapshot_from_row(market: str, row) -> OrderbookSnapshot:
    bids = [Level(p, s) for p, s in json.loads(row["bids"])]
    asks = [Level(p, s) for p, s in json.loads(row["asks"])]
    return OrderbookSnapshot(market=market, timestamp=row["ts"], bids=bids, asks=asks)


@dataclass
class _Stats:
    """Hit count + score distribution for one detector across a replay run."""

    count: int = 0
    score_sum: float = 0.0
    score_max: float = 0.0
    last_message: str = ""

    def add(self, score: float, message: str) -> None:
        self.count += 1
        self.score_sum += score
        self.score_max = max(self.score_max, score)
        self.last_message = message

    @property
    def avg_score(self) -> float:
        return self.score_sum / self.count if self.count else 0.0


@dataclass
class ReplayResult:
    market: str
    snapshots: int
    first_ts: float | None
    last_ts: float | None
    detector_stats: dict[str, _Stats] = field(default_factory=dict)
    risk_stats: _Stats | None = None

    @property
    def span_sec(self) -> float:
        if self.first_ts is None or self.last_ts is None:
            return 0.0
        return self.last_ts - self.first_ts


def replay_market(
    settings, store: SQLiteStore, market: str, limit: int | None = None
) -> ReplayResult:
    """Replay one market's persisted snapshots through a fresh detector stack."""
    rows = store.snapshot_rows(market, limit=limit)
    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None

    interval = max(settings.update_frequency_ms / 1000, 0.001)
    history_len = max(8, int(settings.flicker_window_sec / interval) + 4)
    history: deque = deque(maxlen=history_len)

    detector_stats = {d.name: _Stats() for d in detectors}
    risk_stats = _Stats() if aggregator is not None else None
    first_ts: float | None = None
    last_ts: float | None = None

    for row in rows:
        snapshot = _snapshot_from_row(market, row)
        first_ts = snapshot.timestamp if first_ts is None else first_ts
        last_ts = snapshot.timestamp

        detections = []
        for detector in detectors:
            try:
                found = detector.analyze(snapshot, history)
            except Exception:
                logger.exception("Detector %s raised during replay", detector.name)
                found = []
            for d in found:
                detector_stats[d.detector].add(d.score, d.message)
            detections.extend(found)
        history.append(snapshot)

        if aggregator is not None:
            risk = aggregator.update(market, snapshot.timestamp, detections)
            if risk is not None:
                risk_stats.add(risk.score, risk.message)

    return ReplayResult(
        market=market,
        snapshots=len(rows),
        first_ts=first_ts,
        last_ts=last_ts,
        detector_stats=detector_stats,
        risk_stats=risk_stats,
    )


def format_report(result: ReplayResult) -> str:
    lines = [f"\n📈 {result.market} — {result.snapshots} snapshots"]
    if result.snapshots == 0:
        lines.append("   (no persisted snapshots for this market)")
        return "\n".join(lines)

    lines.append(f"   Span: {result.span_sec:.0f}s")
    width = max((len(name) for name in result.detector_stats), default=0)
    for name, s in result.detector_stats.items():
        if s.count:
            lines.append(
                f"   {name.ljust(width)}  {s.count:>6} hits  "
                f"avg {s.avg_score:.2f}  max {s.score_max:.2f}"
            )
        else:
            lines.append(f"   {name.ljust(width)}  {0:>6} hits")
    if result.risk_stats is not None:
        s = result.risk_stats
        label = "risk-alerts".ljust(width)
        lines.append(f"   {label}  {s.count:>6} hits  avg {s.avg_score:.2f}  max {s.score_max:.2f}")
    return "\n".join(lines)


def run_replay(settings, market: str | None = None, limit: int | None = None) -> int:
    """CLI entry point for ``python main.py --replay``. Returns an exit code."""
    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        print("   Run the watcher with STORAGE_ENABLED=true and PERSIST_SNAPSHOTS=true first.")
        return 1

    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        markets = [market] if market else store.markets_with_snapshots()
        if not markets:
            print("❌ No persisted L2 snapshots found.")
            print("   Set PERSIST_SNAPSHOTS=true, run the watcher for a while, then retry.")
            print("   (Snapshots are high-volume and off by default — see config.example.env.)")
            return 1

        print("🔁 Replay — re-running detectors over persisted snapshots")
        print(f"   DB: {settings.db_path}")
        for m in markets:
            result = replay_market(settings, store, m, limit=limit)
            print(format_report(result))
        return 0
    finally:
        store.close()
