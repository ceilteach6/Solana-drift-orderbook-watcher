"""
src/replay.py

Replay stored L2 snapshots back through the *live* detector stack and risk
aggregator — without waiting on the real market. This is the backtesting tool
from the project roadmap: change a threshold in ``.env``, replay the same
recorded session, and see how the firing counts shift before going live.

Requires the watcher to have previously run with ``STORAGE_ENABLED=true
PERSIST_SNAPSHOTS=true`` so the ``snapshots`` table has full L2 books (the
default ``STORAGE_ENABLED=true`` alone only persists detections + risk, which
isn't enough to re-run the detectors).

    python main.py --replay --market SOL-PERP [--limit N]
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore


@dataclass
class ReplayReport:
    market: str
    snapshot_count: int
    detection_counts: dict[str, int]
    risk_alert_count: int


def _snapshot_from_row(market: str, row) -> OrderbookSnapshot:
    bids = [Level(p, s) for p, s in json.loads(row["bids"])]
    asks = [Level(p, s) for p, s in json.loads(row["asks"])]
    return OrderbookSnapshot(market=market, timestamp=row["ts"], bids=bids, asks=asks)


def run_replay(settings, market: str, limit: int = 0) -> ReplayReport:
    """Re-run stored snapshots for ``market`` through the detector stack.

    Mirrors :meth:`src.watcher.Watcher._tick`'s detect -> aggregate flow so the
    counts here match what a live run would have produced.
    """
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        rows = store.snapshot_rows(market, limit)
    finally:
        store.close()

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None

    interval = max(settings.update_frequency_ms / 1000, 0.001)
    history_len = max(8, int(settings.flicker_window_sec / interval) + 4)

    history: list = []
    detection_counts: dict[str, int] = {}
    risk_alert_count = 0

    for row in rows:
        snapshot = _snapshot_from_row(market, row)
        detections = []
        for detector in detectors:
            found = detector.analyze(snapshot, history)
            detections.extend(found)
            for d in found:
                detection_counts[d.detector] = detection_counts.get(d.detector, 0) + 1
        history.append(snapshot)
        if len(history) > history_len:
            del history[: len(history) - history_len]

        if aggregator is not None and aggregator.update(market, snapshot.timestamp, detections) is not None:
            risk_alert_count += 1

    return ReplayReport(
        market=market,
        snapshot_count=len(rows),
        detection_counts=detection_counts,
        risk_alert_count=risk_alert_count,
    )


def format_report(report: ReplayReport, settings) -> str:
    lines = [f"🔁 Replay report — {report.market} ({report.snapshot_count} snapshots)"]
    if report.snapshot_count == 0:
        lines.append("   No stored snapshots found.")
        lines.append("   Run the watcher with STORAGE_ENABLED=true PERSIST_SNAPSHOTS=true first.")
        return "\n".join(lines)

    if report.detection_counts:
        width = max(len(k) for k in report.detection_counts)
        for name, count in sorted(report.detection_counts.items()):
            lines.append(f"   {name.ljust(width)} : {count} firings")
    else:
        lines.append("   (no detector fired)")

    if settings.risk_aggregation:
        lines.append(
            f"   risk alerts (≥{settings.risk_alert_threshold:.2f}, smoothed) : "
            f"{report.risk_alert_count}"
        )
    return "\n".join(lines)


def replay_main(settings, market: str, limit: int = 0) -> int:
    """Run, print the report, and return a process exit code."""
    report = run_replay(settings, market, limit)
    print(format_report(report, settings))
    return 0 if report.snapshot_count > 0 else 1
