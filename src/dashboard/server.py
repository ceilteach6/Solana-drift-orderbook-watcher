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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.storage import SQLiteStore

logger = logging.getLogger(__name__)

_INDEX_HTML = os.path.join(os.path.dirname(__file__), "index.html")


def _make_handler(db_path: str):
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

            if route in ("/", "/index.html"):
                return self._send_html()

            params = parse_qs(parsed.query)
            market = (params.get("market") or [""])[0]
            try:
                limit = int((params.get("limit") or ["2000"])[0])
            except (ValueError, TypeError):
                return self._send_json({"error": "limit must be an integer"}, 400)

            # Each request opens its own short-lived read connection.
            store = SQLiteStore(db_path)
            try:
                store.connect()
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
            finally:
                store.close()

    return Handler


def run_dashboard(settings) -> int:
    """Serve the dashboard until interrupted. Returns a process exit code."""
    if not os.path.exists(settings.db_path):
        print(f"❌ DB not found: {settings.db_path}")
        print("   Run the watcher with STORAGE_ENABLED=true first.")
        return 1

    handler = _make_handler(settings.db_path)
    server = ThreadingHTTPServer(
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
    return 0
