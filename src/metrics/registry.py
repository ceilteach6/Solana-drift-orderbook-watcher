"""
src/metrics/registry.py

A minimal, dependency-free Prometheus metrics registry.

Only the two metric types the watcher actually needs are implemented —
monotonic ``Counter`` and point-in-time ``Gauge`` — rendered in the standard
Prometheus text exposition format (https://prometheus.io/docs/instrumenting/exposition_formats/).
No ``prometheus_client`` dependency: the watcher's own philosophy (stdlib
only, see ``src/dashboard/server.py``) applies here too.

Thread-safe: ``inc``/``set``/``render`` all take a lock, because metrics are
written from the watcher's asyncio loop and read from the HTTP server's
request-handling threads concurrently.
"""

from __future__ import annotations

import threading

# Prometheus metric/label names: [a-zA-Z_:][a-zA-Z0-9_:]*
_NAME_OK = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:"
)


def _check_name(name: str, what: str) -> None:
    if not name or name[0].isdigit() or any(c not in _NAME_OK for c in name):
        raise ValueError(f"Invalid Prometheus {what}: {name!r}")


def _escape(value: str) -> str:
    # Per the exposition format: backslash and quote must be escaped, and a
    # literal newline in a label value would otherwise corrupt line framing.
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class _Metric:
    def __init__(self, name: str, help_text: str, label_names: tuple[str, ...] = ()):
        _check_name(name, "metric name")
        for label in label_names:
            _check_name(label, "label name")
        self.name = name
        self.help_text = help_text
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}
        self._lock = threading.Lock()

    def _key(self, labels: dict[str, str]) -> tuple[str, ...]:
        missing = set(self.label_names) - labels.keys()
        if missing:
            raise ValueError(f"{self.name}: missing labels {sorted(missing)}")
        extra = labels.keys() - set(self.label_names)
        if extra:
            raise ValueError(f"{self.name}: unexpected labels {sorted(extra)}")
        return tuple(str(labels[label]) for label in self.label_names)

    def _render_lines(self, metric_type: str) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} {metric_type}"]
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            if self.label_names:
                pairs = ",".join(
                    f'{label}="{_escape(v)}"' for label, v in zip(self.label_names, key)
                )
                lines.append(f"{self.name}{{{pairs}}} {value!r}")
            else:
                lines.append(f"{self.name} {value!r}")
        return lines


class Counter(_Metric):
    """A value that only ever goes up (resets to 0 on process restart)."""

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError(f"Counter.inc() amount must be >= 0, got {amount}")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def render(self) -> list[str]:
        return self._render_lines("counter")


class Gauge(_Metric):
    """A value that can go up or down, reflecting current state."""

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def render(self) -> list[str]:
        return self._render_lines("gauge")


class Registry:
    """Owns the set of metrics exposed at ``/metrics``."""

    def __init__(self) -> None:
        self._metrics: list[_Metric] = []
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> Counter:
        metric = Counter(name, help_text, label_names)
        with self._lock:
            self._metrics.append(metric)
        return metric

    def gauge(self, name: str, help_text: str, label_names: tuple[str, ...] = ()) -> Gauge:
        metric = Gauge(name, help_text, label_names)
        with self._lock:
            self._metrics.append(metric)
        return metric

    def render(self) -> str:
        """Prometheus text exposition format for all registered metrics."""
        with self._lock:
            metrics = list(self._metrics)
        lines: list[str] = []
        for metric in metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n" if lines else ""
