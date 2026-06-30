"""
src/metrics/format.py

Renders a :class:`~src.metrics.registry.MetricsRegistry` snapshot as
Prometheus text exposition format (https://prometheus.io/docs/instrumenting/exposition_formats/).
"""

from __future__ import annotations

from src.metrics.registry import MetricsRegistry

# name -> (HELP text, TYPE). Metrics not listed here still render, just
# without a HELP/TYPE preamble — new metrics don't need to touch this file.
_METRIC_DOCS: dict[str, tuple[str, str]] = {
    "drift_watcher_snapshots_total": (
        "Orderbook snapshots successfully polled, by market.", "counter",
    ),
    "drift_watcher_snapshot_errors_total": (
        "Snapshot polls that raised an exception, by market.", "counter",
    ),
    "drift_watcher_detections_total": (
        "Detector findings emitted, by market and detector.", "counter",
    ),
    "drift_watcher_detector_errors_total": (
        "Detector runs that raised an exception, by detector.", "counter",
    ),
    "drift_watcher_alerts_total": (
        "Alerts dispatched, by market.", "counter",
    ),
    "drift_watcher_risk_score": (
        "Current smoothed risk score per market (0..1).", "gauge",
    ),
    "drift_watcher_healthcheck_failures_total": (
        "Self-test health-checks that found a non-firing detector.", "counter",
    ),
}


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_value(value: float) -> str:
    # Prometheus parses ints and floats the same way; avoid "1.0" noise for
    # whole-number counters while still rendering fractional gauges exactly.
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    parts = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
    return "{" + parts + "}"


def render(registry: MetricsRegistry) -> str:
    """Render the current registry state as Prometheus exposition text."""
    counters, gauges = registry.snapshot()
    lines: list[str] = []
    seen_help: set[str] = set()

    for store in (counters, gauges):
        for (name, labels), value in sorted(store.items()):
            if name not in seen_help:
                seen_help.add(name)
                help_text, metric_type = _METRIC_DOCS.get(name, ("", "untyped"))
                if help_text:
                    lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} {metric_type}")
            lines.append(f"{name}{_format_labels(labels)} {_format_value(value)}")

    return "\n".join(lines) + "\n" if lines else ""
