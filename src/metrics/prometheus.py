"""
src/metrics/prometheus.py

Optional Prometheus metrics exporter.

Enable by setting METRICS_PORT in your .env:
    METRICS_PORT=8000   →  scrape at http://localhost:8000/metrics

Requires: prometheus_client>=0.17.0
The package is listed in requirements.txt as optional; this module degrades to a
no-op when it is absent so the watcher runs without it.

Exported metrics
----------------
drift_watcher_detections_total{detector, market}
    Counter — number of individual pattern detections since startup.

drift_watcher_risk_ema_score{market}
    Gauge — current exponential-moving-average risk score (0.0 – 1.0).

drift_watcher_alerts_emitted_total{market}
    Counter — number of score-filtered alerts sent to all sinks.

drift_watcher_ticks_total{market}
    Counter — number of successful polling ticks completed.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

try:
    import prometheus_client as _prom
    _AVAILABLE = True
except ImportError:  # pragma: no cover — tested via mock in test_metrics.py
    _prom = None  # type: ignore[assignment]
    _AVAILABLE = False


class MetricsExporter:
    """Prometheus metrics exporter.

    Instantiating with ``metrics_port=0`` (or without prometheus_client installed)
    produces a no-op object — every method is safe to call unconditionally.
    """

    def __init__(self, settings) -> None:
        port = getattr(settings, "metrics_port", 0)
        self._active = _AVAILABLE and port > 0
        self._port = port

        if not self._active:
            return

        # Use a private registry to avoid collisions when multiple instances
        # exist (e.g. in tests).
        self._registry = _prom.CollectorRegistry()

        self._detections_total = _prom.Counter(
            "drift_watcher_detections_total",
            "Number of individual pattern detections fired",
            ["detector", "market"],
            registry=self._registry,
        )
        self._risk_score = _prom.Gauge(
            "drift_watcher_risk_ema_score",
            "Current EMA risk score per market (0.0 – 1.0)",
            ["market"],
            registry=self._registry,
        )
        self._alerts_total = _prom.Counter(
            "drift_watcher_alerts_emitted_total",
            "Number of score-filtered alerts delivered to all sinks",
            ["market"],
            registry=self._registry,
        )
        self._ticks_total = _prom.Counter(
            "drift_watcher_ticks_total",
            "Number of successful orderbook polling ticks",
            ["market"],
            registry=self._registry,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Start the HTTP metrics server in a background daemon thread.

        Daemon threads die with the main process, so no explicit shutdown is
        needed. Calling this when already started or when inactive is a no-op.
        """
        if not self._active:
            return
        threading.Thread(
            target=_prom.start_http_server,
            args=(self._port,),
            kwargs={"registry": self._registry},
            daemon=True,
            name="metrics-http",
        ).start()
        logger.info("Prometheus metrics available at http://localhost:%d/metrics", self._port)

    # ------------------------------------------------------------------ #
    # Recording helpers (all are no-ops when inactive)
    # ------------------------------------------------------------------ #

    def record_detections(self, detections) -> None:
        """Increment detection counters for each Detection in *detections*."""
        if not self._active:
            return
        for d in detections:
            self._detections_total.labels(detector=d.detector, market=d.market).inc()

    def record_risk_score(self, market: str, score: float) -> None:
        """Set the EMA risk score gauge for *market*."""
        if not self._active:
            return
        self._risk_score.labels(market=market).set(score)

    def record_alerts(self, market: str, count: int) -> None:
        """Add *count* to the alerts-emitted counter for *market*."""
        if not self._active or count <= 0:
            return
        self._alerts_total.labels(market=market).inc(count)

    def record_tick(self, market: str) -> None:
        """Increment the tick counter for *market*."""
        if not self._active:
            return
        self._ticks_total.labels(market=market).inc()
