"""
src/replay.py

Replay / backtesting: re-run the real detector stack + risk aggregator over
snapshots the watcher already persisted (``STORAGE_ENABLED=true`` +
``PERSIST_SNAPSHOTS=true``), instead of waiting for a pattern to recur live.

Because it drives the *same* ``DEFAULT_DETECTORS`` and :class:`RiskAggregator`
classes the live watcher uses — just fed from stored snapshots instead of the
DLOB feed — a candidate threshold change can be evaluated against real market
history before it goes live: build a modified ``Settings`` (e.g. via
``dataclasses.replace(settings, flicker_min_events=5)``) and pass it in.

Usage:
    python main.py --replay
    python main.py --replay --market=SOL-PERP
    python main.py --replay --market=SOL-PERP --from=2026-06-30T00:00:00 --to=2026-06-30T12:00:00

``--from``/``--to`` are ISO-8601 timestamps, interpreted as UTC when no offset
is given (snapshots are stored as UTC epoch seconds).
"""

from __future__ import annotations

import dataclasses
import os
from collections import deque
from datetime import datetime, timezone

from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator


@dataclasses.dataclass
class ReplaySummary:
    market: str
    ticks: int
    detections_by_detector: dict
    risk_alerts: int

    def report(self) -> str:
        lines = [f"⏪ Replay — {self.market} ({self.ticks} snapshots)"]
        if not self.detections_by_detector:
            lines.append("   (no detections fired)")
        for name, count in sorted(self.detections_by_detector.items()):
            lines.append(f"   {name.ljust(15)}: {count}")
        lines.append(f"   risk alerts    : {self.risk_alerts}")
        return "\n".join(lines)


def replay_market(settings, store, market: str, *, start_ts=None, end_ts=None) -> ReplaySummary:
    """Feed persisted snapshots for ``market`` through the live detector stack
    (and risk aggregator, if enabled) in timestamp order.

    A fresh detector/aggregator/history state is used per call, mirroring how
    the watcher starts clean per market — a replay run's results don't depend
    on whatever ran before it.
    """
    # Imported lazily: watcher.py -> storage -> (nothing) -> replay would be
    # fine, but importing history_length from watcher at module load time
    # would make every ``import src.replay`` also import asyncio/logging
    # machinery it doesn't need.
    from src.watcher import history_length

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
    history: deque = deque(maxlen=history_length(settings))

    by_detector: dict = {}
    risk_alerts = 0
    ticks = 0

    for snapshot in store.snapshots(market, start_ts=start_ts, end_ts=end_ts):
        ticks += 1
        detections = []
        for detector in detectors:
            detections.extend(detector.analyze(snapshot, history))
        for d in detections:
            by_detector[d.detector] = by_detector.get(d.detector, 0) + 1
        if aggregator is not None:
            risk = aggregator.update(market, snapshot.timestamp, detections)
            if risk is not None:
                risk_alerts += 1
        history.append(snapshot)

    return ReplaySummary(market, ticks, by_detector, risk_alerts)


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _parse_args(argv: list) -> dict:
    opts = {"market": None, "from": None, "to": None}
    for arg in argv:
        for key in opts:
            prefix = f"--{key}="
            if arg.startswith(prefix):
                opts[key] = arg[len(prefix):]
    return opts


def replay_main(settings, argv: list) -> int:
    """Entry point for ``python main.py --replay ...``. Returns a process
    exit code (0 = at least one market replayed with data, 1 = nothing to
    replay or a bad range)."""
    if not settings.storage_enabled:
        print("❌ STORAGE_ENABLED=false — nothing persisted to replay.")
        return 1
    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        return 1

    opts = _parse_args(argv)
    try:
        start_ts = _parse_ts(opts["from"])
        end_ts = _parse_ts(opts["to"])
    except ValueError as exc:
        print(f"❌ Invalid --from/--to timestamp: {exc}")
        return 1
    if start_ts is not None and end_ts is not None and start_ts > end_ts:
        print("❌ --from must be before --to.")
        return 1

    from src.storage import SQLiteStore

    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        markets = [opts["market"]] if opts["market"] else store.snapshot_markets()
        if not markets:
            print(
                "❌ No persisted snapshots found. Replay needs "
                "PERSIST_SNAPSHOTS=true while the watcher ran (STORAGE_ENABLED "
                "alone only keeps detections/risk, not the L2 book)."
            )
            return 1

        exit_code = 1
        for market in markets:
            summary = replay_market(settings, store, market, start_ts=start_ts, end_ts=end_ts)
            if summary.ticks == 0:
                print(f"⚠️  {market}: no persisted snapshots in range.")
                continue
            print(summary.report())
            exit_code = 0
        return exit_code
    finally:
        store.close()
