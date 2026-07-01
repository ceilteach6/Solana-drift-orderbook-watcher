"""
tests/test_dashboard.py

Integration test for the dashboard HTTP server against a real (temp-file)
SQLite store: verifies the shared-connection handler serves concurrent
requests correctly and that ?limit= is clamped.
"""

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer

from src.dashboard.server import _make_handler
from src.storage import SQLiteStore


def _start_server(db_path):
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5, mid=100.0)
    store.record_risk("SOL-PERP", 2.0, 0.6, mid=101.0)

    lock = threading.Lock()
    handler = _make_handler(store, lock)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, store, thread


def _get(server, path):
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_concurrent_requests_share_one_connection(tmp_path):
    server, store, thread = _start_server(str(tmp_path / "dash.db"))
    try:
        # Fire many concurrent requests; a shared connection + lock must
        # serve all of them correctly without corrupting responses.
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: _get(server, "/api/markets"), range(20)))
        assert all(status == 200 for status, _ in results)
        assert all(body == ["SOL-PERP"] for _, body in results)
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_series_endpoint_returns_recorded_points(tmp_path):
    server, store, thread = _start_server(str(tmp_path / "dash2.db"))
    try:
        status, body = _get(server, "/api/series?market=SOL-PERP")
        assert status == 200
        assert len(body["price"]) == 2
        assert len(body["risk"]) == 2
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_limit_out_of_range_is_rejected(tmp_path):
    server, store, thread = _start_server(str(tmp_path / "dash3.db"))
    try:
        status, body = _get(server, "/api/series?market=SOL-PERP&limit=999999")
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()
        store.close()
