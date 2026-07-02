"""Prometheus metrics layer (opt-in via METRICS_ENABLED)."""

from src.metrics.registry import Counter, Gauge, Registry
from src.metrics.server import MetricsServer, start_metrics_server
from src.metrics.watcher_metrics import WatcherMetrics

__all__ = [
    "Counter",
    "Gauge",
    "Registry",
    "WatcherMetrics",
    "MetricsServer",
    "start_metrics_server",
]
