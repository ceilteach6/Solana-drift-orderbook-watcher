"""
src/metrics/server.py

Minimal stdlib HTTP server exposing ``MetricsRegistry`` at ``/metrics`` in
Prometheus text format. Runs in a background daemon thread inside the watcher
process so the counters/gauges reflect live state — unlike the dashboard,
which only ever sees what has already been written to SQLite.
"""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.metrics.format import render
from src.metrics.registry import MetricsRegistry

logger = logging.getLogger(__name__)


def _make_handler(registry: MetricsRegistry):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet default access logging
            return

        def do_GET(self):
            if self.path not in ("/", "/metrics"):
                self.send_error(404, "not found")
                return
            body = render(registry).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def start_metrics_server(registry: MetricsRegistry, host: str, port: int) -> ThreadingHTTPServer:
    """Bind and start the exporter on a background daemon thread.

    The caller owns the returned server's lifecycle — call ``shutdown()``
    then ``server_close()`` to stop it (see ``Watcher.start``).
    """
    server = ThreadingHTTPServer((host, port), _make_handler(registry))
    thread = threading.Thread(
        target=server.serve_forever, name="metrics-exporter", daemon=True
    )
    thread.start()
    logger.info("Metrics exporter listening on http://%s:%d/metrics", host, port)
    return server
