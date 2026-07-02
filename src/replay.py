"""
src/replay.py

Replay / backtesting: re-runs the live detector + risk-aggregator stack over
*previously persisted* snapshots instead of a live feed. Useful for tuning
detector thresholds against recorded market data, and for sanity-checking a
threshold change against a known session before deploying it live.

    python main.py --replay SOL-PERP [--replay-limit 5000]

Requires ``STORAGE_ENABLED=true`` **and** ``PERSIST_SNAPSHOTS=true`` to have
been set while the data was recorded — the ``detections``/``risk`` tables
alone aren't enough to replay, since detectors need the raw L2 book, not just
the score they produced from it last time.

Read-only: this never writes back to the store, so re-running it (e.g. after
tweaking a threshold in ``.env``) is always safe.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from src.detector import DEFAULT_DETECTORS
from src.risk import RiskAggregator
from src.storage import SQLiteStore
from src.watcher import history_length


@dataclass
class DetectorStats:
    name: str
    fired: int = 0
    max_score: float = 0.0
    total_score: float = 0.0

    @property
    def avg_score(self) -> float:
        return self.total_score / self.fired if self.fired else 0.0


@dataclass
class ReplayReport:
    market: str
    ticks: int
    detector_stats: dict[str, DetectorStats] = field(default_factory=dict)
    risk_alerts: int = 0
    final_risk: float = 0.0


def replay_market(settings, market: str, *, limit: int = 5000) -> ReplayReport:
    """Replay persisted snapshots for ``market`` through a fresh detector +
    risk stack (built from the *current* ``settings``, so a threshold edited
    since recording is honored). Never mutates the store."""
    store = SQLiteStore(settings.db_path)
    store.connect()
    try:
        snapshots = store.load_snapshots(market, limit=limit)
    finally:
        store.close()

    detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
    aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
    history: deque = deque(maxlen=history_length(settings))
    stats = {d.name: DetectorStats(d.name) for d in detectors}
    report = ReplayReport(market=market, ticks=len(snapshots), detector_stats=stats)

    for snapshot in snapshots:
        detections = []
        for detector in detectors:
            for d in detector.analyze(snapshot, history):
                detections.append(d)
                s = stats[d.detector]
                s.fired += 1
                s.total_score += d.score
                s.max_score = max(s.max_score, d.score)
        history.append(snapshot)

        if aggregator is not None and aggregator.update(market, snapshot.timestamp, detections) is not None:
            report.risk_alerts += 1

    if aggregator is not None:
        report.final_risk = aggregator.score(market)
    return report


def format_report(report: ReplayReport) -> str:
    lines = [f"⏪ Replay report — {report.market} ({report.ticks} ticks)"]
    if report.ticks == 0:
        lines.append(
            "   No persisted snapshots found. Requires STORAGE_ENABLED=true "
            "and PERSIST_SNAPSHOTS=true while the watcher was running."
        )
        return "\n".join(lines)

    width = max((len(n) for n in report.detector_stats), default=0)
    for name, s in report.detector_stats.items():
        lines.append(
            f"   {name.ljust(width)}  fired {s.fired:4d}x  "
            f"max {s.max_score:.2f}  avg {s.avg_score:.2f}"
        )
    lines.append(
        f"   risk alerts: {report.risk_alerts}  "
        f"(final smoothed risk {report.final_risk:.2f})"
    )
    return "\n".join(lines)


def replay_main(settings, market: str, *, limit: int = 5000) -> int:
    """Run, print the report, and return a process exit code (0 = data found)."""
    report = replay_market(settings, market, limit=limit)
    print(format_report(report))
    return 0 if report.ticks > 0 else 1
