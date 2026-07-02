"""
src/metrics/server.py

A tiny, dependency-free ``/metrics`` HTTP endpoint (stdlib ``http.server``)
for Prometheus to scrape, mirroring ``src/dashboard/server.py``'s approach.

Runs in a background daemon thread alongside the watcher's asyncio poll
loop — started by :meth:`src.watcher.Watcher.start` when
``METRICS_ENABLED=true`` and stopped in its ``finally`` block.
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


def _make_handler(registry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet default logging
            return

        def do_GET(self):
            if self.path not in ("/metrics", "/metrics/"):
                body = b"not found"
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = registry.render().encode("utf-8")
            self.send_response(200)
            # Standard Prometheus exposition content type.
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


class MetricsServer:
    """Owns the background HTTP server thread; ``stop()`` is idempotent."""

    def __init__(self, settings, registry) -> None:
        self._server = ThreadingHTTPServer(
            (settings.metrics_host, settings.metrics_port), _make_handler(registry)
        )
        # Same rationale as the dashboard server: don't let a slow in-flight
        # request thread block process shutdown.
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="metrics-server", daemon=True
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/metrics"

    def start(self) -> None:
        self._thread.start()
        logger.info("Metrics endpoint listening on %s", self.url)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def start_metrics_server(settings, registry) -> MetricsServer:
    server = MetricsServer(settings, registry)
    server.start()
    return server
