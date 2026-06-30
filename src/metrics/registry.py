"""
src/metrics/registry.py

Thread-safe in-process counters/gauges for the optional Prometheus exporter.
Written from the watcher's polling loop and read back by the exporter's HTTP
handler, which runs on a different thread (see ``src/metrics/server.py``) —
hence the lock.
"""

from __future__ import annotations

import threading

_Key = tuple[str, tuple[tuple[str, str], ...]]


def _key(name: str, labels: dict[str, str] | None) -> _Key:
    return (name, tuple(sorted((labels or {}).items())))


class MetricsRegistry:
    """A minimal counter/gauge store, just enough for Prometheus exposition."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[_Key, float] = {}
        self._gauges: dict[_Key, float] = {}

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        key = _key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set(self, name: str, labels: dict[str, str] | None = None, value: float = 0.0) -> None:
        key = _key(name, labels)
        with self._lock:
            self._gauges[key] = value

    def snapshot(self) -> tuple[dict[_Key, float], dict[_Key, float]]:
        """A point-in-time copy, safe to render without holding the lock."""
        with self._lock:
            return dict(self._counters), dict(self._gauges)
