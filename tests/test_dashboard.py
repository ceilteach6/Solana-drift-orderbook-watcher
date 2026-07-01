"""
tests/test_dashboard.py

Tests the dashboard's read-only HTTP API, in particular that the ``?limit=``
query param is clamped: SQLite treats a negative LIMIT as "no limit", so an
unclamped value would let a client dump an entire table in one request.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from src.dashboard import server as dashboard_server
from src.storage import SQLiteStore


def _start_server(db_path, monkeypatch, max_limit):
    monkeypatch.setattr(dashboard_server, "_MAX_LIMIT", max_limit)
    handler = dashboard_server._make_handler(db_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return json.loads(resp.read())


def test_series_limit_is_capped(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    store.connect()
    for i in range(10):
        store.record_risk("SOL-PERP", float(i), 0.5)
    store.close()

    httpd, thread = _start_server(db_path, monkeypatch, max_limit=5)
    try:
        port = httpd.server_address[1]

        data = _get(port, f"/api/series?market=SOL-PERP&limit=999999")
        assert len(data["risk"]) == 5  # clamped to the ceiling, not the 10 rows present

        # A negative limit would mean "no limit" to SQLite if passed through raw;
        # it must instead clamp up to the floor (1), not bypass the cap.
        data = _get(port, "/api/series?market=SOL-PERP&limit=-1")
        assert len(data["risk"]) == 1
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def test_series_limit_has_a_floor(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5)
    store.close()

    httpd, thread = _start_server(db_path, monkeypatch, max_limit=5)
    try:
        port = httpd.server_address[1]
        data = _get(port, "/api/series?market=SOL-PERP&limit=0")
        assert len(data["risk"]) == 1  # limit=0 clamps up to the floor, not "no rows"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
