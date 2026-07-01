"""
src/dashboard/server.py

A tiny, dependency-free dashboard server (stdlib ``http.server``) that reads the
SQLite time-series and serves it to a TradingView Lightweight-Charts frontend.

Endpoints:
- ``GET /``                       → the chart page (index.html)
- ``GET /api/markets``            → ["SOL-PERP", ...]
- ``GET /api/series?market=...``  → {"price": [...], "risk": [...]}
- ``GET /api/detections?market=`` → [{"time","detector","score","message"}, ...]

Start it with:  ``python main.py --dashboard``

It only *reads* the DB (WAL mode lets the watcher keep writing concurrently),
so you can run the watcher and the dashboard side by side.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.storage import SQLiteStore

logger = logging.getLogger(__name__)

_INDEX_HTML = os.path.join(os.path.dirname(__file__), "index.html")

# ThreadingHTTPServer spawns one native OS thread per connection with no cap.
# Bound how many requests can be doing real work (DB queries) at once so a
# burst of requests degrades to queueing instead of unbounded thread growth.
_MAX_CONCURRENT_REQUESTS = 32


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    def __init__(self, *args, max_concurrent=_MAX_CONCURRENT_REQUESTS, **kwargs):
        super().__init__(*args, **kwargs)
        self._concurrency = threading.BoundedSemaphore(max_concurrent)

    def process_request_thread(self, request, client_address):
        with self._concurrency:
            super().process_request_thread(request, client_address)


def _make_handler(store: SQLiteStore, store_lock: threading.Lock):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet default logging
            return

        def _send_json(self, payload, status=200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self):
            try:
                with open(_INDEX_HTML, "rb") as fh:
                    body = fh.read()
            except OSError:
                self.send_error(500, "index.html missing")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            route = parsed.path
            params = parse_qs(parsed.query)
            market = (params.get("market") or [""])[0]
            try:
                limit = int((params.get("limit") or ["2000"])[0])
            except (ValueError, TypeError):
                return self._send_json({"error": "limit must be an integer"}, 400)
            # SQLite treats a negative LIMIT as "no limit", and an unbounded
            # positive value lets a client force a multi-million-row scan —
            # clamp to a sane range instead of trusting the query string.
            if not (1 <= limit <= 5000):
                return self._send_json({"error": "limit must be between 1 and 5000"}, 400)

            if route in ("/", "/index.html"):
                return self._send_html()

            # Shared connection across requests/threads: reconnecting (and
            # re-running the full schema DDL) on every request was wasteful
            # and, combined with ThreadingHTTPServer's unbounded thread-per-
            # connection behavior, a real amplification vector if the
            # dashboard is ever bound to a non-loopback host. sqlite3
            # connections aren't safe for concurrent use from multiple
            # threads even with check_same_thread=False, so serialize access.
            try:
                with store_lock:
                    if route == "/api/markets":
                        return self._send_json(store.markets())
                    if route == "/api/series":
                        if not market:
                            return self._send_json({"error": "market required"}, 400)
                        return self._send_json({
                            "price": store.price_series(market, limit),
                            "risk": store.risk_series(market, limit),
                        })
                    if route == "/api/detections":
                        if not market:
                            return self._send_json({"error": "market required"}, 400)
                        return self._send_json(store.detection_markers(market, limit))
                return self._send_json({"error": "not found"}, 404)
            except Exception as exc:
                logger.exception("dashboard request failed")
                return self._send_json({"error": str(exc)}, 500)

    return Handler


def run_dashboard(settings) -> int:
    """Serve the dashboard until interrupted. Returns a process exit code."""
    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        print("   Run the watcher with STORAGE_ENABLED=true first.")
        return 1

    store = SQLiteStore(settings.db_path)
    store.connect()
    handler = _make_handler(store, threading.Lock())
    server = BoundedThreadingHTTPServer(
        (settings.dashboard_host, settings.dashboard_port), handler
    )
    url = f"http://{settings.dashboard_host}:{settings.dashboard_port}"
    print(f"📊 Dashboard on {url}  (DB: {settings.db_path})")
    print("   Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n⏹️  Dashboard stopped.")
    finally:
        server.server_close()
        store.close()
    return 0
