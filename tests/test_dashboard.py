"""
tests/test_dashboard.py

The dashboard's /api/series and /api/detections endpoints pass a client-supplied
``limit`` straight to a SQLite "LIMIT ?" clause. SQLite treats a negative LIMIT
as "no limit", so an unvalidated negative/huge value can dump an entire table
into one JSON response. These tests cover the bound added in server.py.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from src.dashboard.server import _make_handler
from src.storage import SQLiteStore


def start_server(tmp_path):
    db_path = str(tmp_path / "dash.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5, mid=100.0)
    store.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(db_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, port


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as resp:
        return resp.status, json.loads(resp.read())


def get_error(port, path):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
        raise AssertionError("expected HTTPError")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_negative_limit_is_rejected(tmp_path):
    server, port = start_server(tmp_path)
    try:
        status, body = get_error(port, "/api/series?market=SOL-PERP&limit=-1")
        assert status == 400
        assert "limit" in body["error"]
    finally:
        server.shutdown()


def test_oversized_limit_is_rejected(tmp_path):
    server, port = start_server(tmp_path)
    try:
        status, body = get_error(port, "/api/series?market=SOL-PERP&limit=999999999")
        assert status == 400
        assert "limit" in body["error"]
    finally:
        server.shutdown()


def test_valid_limit_succeeds(tmp_path):
    server, port = start_server(tmp_path)
    try:
        status, body = get(port, "/api/series?market=SOL-PERP&limit=100")
        assert status == 200
        assert len(body["price"]) == 1
    finally:
        server.shutdown()
