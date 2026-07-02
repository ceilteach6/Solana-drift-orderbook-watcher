"""
src/metrics.py

Minimal, dependency-free Prometheus exposition-format metrics registry + HTTP
server (roadmap item: "Prometheus metrics exporter" in docs/NOTES.md).

Counters/gauges are updated in-process by the watcher on its hot path (a
handful of dict lookups per tick — negligible overhead) and served in the
Prometheus text exposition format, so an existing Prometheus/Grafana stack can
scrape this process directly. No extra dependency, no separate exporter
process, no link/token for the user to provide — point Prometheus at
``http://<host>:<port>/metrics`` and it works.

Enable with ``METRICS_ENABLED=true`` (``METRICS_HOST``/``METRICS_PORT``,
default ``127.0.0.1:9090``).
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# name -> (kind, help text). Metrics not listed here still render (with an
# empty HELP line) instead of being dropped, so a future counter/gauge can't
# be silently swallowed by an outdated table.
_HELP: dict[str, tuple[str, str]] = {
    "watcher_ticks_total": ("counter", "Orderbook polls that returned a snapshot, per market."),
    "watcher_snapshot_errors_total": ("counter", "Polls that raised or timed out, per market."),
    "watcher_detections_total": ("counter", "Detector findings emitted, per market and detector."),
    "watcher_alerts_emitted_total": ("counter", "Alerts dispatched to sinks (post score-filter), per market."),
    "watcher_risk_score": ("gauge", "Current smoothed risk score in [0, 1], per market."),
    "watcher_storage_errors_total": ("counter", "Failed storage writes, per market."),
}


def _label_str(labels: dict) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


class MetricsRegistry:
    """Thread-safe counters/gauges, rendered in Prometheus text format.

    One instance is owned by the :class:`~src.watcher.Watcher` and updated
    from the asyncio event-loop thread; ``render()`` is called from the HTTP
    server's request-handling threads. The lock keeps both sides consistent
    without requiring the metrics server to share the event loop.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple, float] = {}
        self._gauges: dict[tuple, float] = {}

    @staticmethod
    def _key(labels: dict | None) -> tuple:
        return tuple(sorted((labels or {}).items()))

    def inc(self, name: str, labels: dict | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        with self._lock:
            self._counters[(name, key)] = self._counters.get((name, key), 0.0) + amount

    def set(self, name: str, labels: dict | None = None, value: float = 0.0) -> None:
        key = self._key(labels)
        with self._lock:
            self._gauges[(name, key)] = value

    def render(self) -> str:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)

        lines: list[str] = []
        seen_help: set[str] = set()
        for store, default_kind in ((counters, "counter"), (gauges, "gauge")):
            for name, label_items in sorted(store.keys()):
                if name not in seen_help:
                    kind, help_text = _HELP.get(name, (default_kind, ""))
                    lines.append(f"# HELP {name} {help_text}".rstrip())
                    lines.append(f"# TYPE {name} {kind}")
                    seen_help.add(name)
                value = store[(name, label_items)]
                lines.append(f"{name}{_label_str(dict(label_items))} {value}")
        return "\n".join(lines) + "\n" if lines else ""


def _make_handler(registry: MetricsRegistry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet default access logging
            return

        def do_GET(self):
            if self.path.split("?", 1)[0].rstrip("/") not in ("", "/metrics"):
                self.send_response(404)
                self.end_headers()
                return
            body = registry.render().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def start_metrics_server(registry: MetricsRegistry, host: str, port: int) -> ThreadingHTTPServer:
    """Start serving ``registry`` on a background daemon thread.

    Returns the bound server so the caller can ``shutdown()`` /
    ``server_close()`` it on watcher shutdown. A daemon thread means a stuck
    scrape can't block process exit, matching the dashboard server's
    daemon_threads behavior.
    """
    server = ThreadingHTTPServer((host, port), _make_handler(registry))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="metrics-http", daemon=True)
    thread.start()
    return server
