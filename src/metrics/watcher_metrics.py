"""
src/metrics/watcher_metrics.py

The watcher's fixed set of Prometheus metrics. One :class:`WatcherMetrics`
instance is created per :class:`~src.watcher.Watcher` and threaded through
the poll loop to record what happened on every tick — separate from (and
much cheaper than) the SQLite time-series in ``src/storage``: this is for
live operational monitoring/alerting on the watcher process itself (is it
polling, erroring, falling behind), not for analysing market data.
"""

from __future__ import annotations

from src.metrics.registry import Registry


class WatcherMetrics:
    def __init__(self) -> None:
        self.registry = Registry()

        self.ticks_total = self.registry.counter(
            "watcher_ticks_total",
            "Snapshots successfully polled and run through the detector stack.",
            ("market",),
        )
        self.snapshot_errors_total = self.registry.counter(
            "watcher_snapshot_errors_total",
            "Snapshot polls that failed or timed out.",
            ("market",),
        )
        self.detections_total = self.registry.counter(
            "watcher_detections_total",
            "Raw detector findings, before risk aggregation or score filtering.",
            ("detector", "market"),
        )
        self.alerts_emitted_total = self.registry.counter(
            "watcher_alerts_emitted_total",
            "Alerts that passed ALERT_MIN_SCORE and were dispatched to sinks.",
            ("market",),
        )
        self.risk_score = self.registry.gauge(
            "watcher_risk_score",
            "Current smoothed risk score per market (0..1); only set when RISK_AGGREGATION is on.",
            ("market",),
        )
        self.storage_errors_total = self.registry.counter(
            "watcher_storage_errors_total",
            "Failed SQLite persistence writes.",
            ("market",),
        )
        self.healthcheck_failures_total = self.registry.counter(
            "watcher_healthcheck_failures_total",
            "Self-test health-check cycles where at least one detector failed to fire.",
        )
