"""
tests/test_dashboard.py

Exercises the dashboard's tiny stdlib HTTP server end-to-end, guarding against
a client-supplied ``limit`` query param forcing an unbounded database read
(e.g. ``?limit=999999999``).
"""

import json
import threading
import urllib.error
import urllib.request

from http.server import ThreadingHTTPServer

from src.dashboard.server import _make_handler
from src.storage import SQLiteStore


def start_server(db_path):
    handler = _make_handler(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def get_json(server, path):
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=5
        ) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def make_populated_store(tmp_path):
    store = SQLiteStore(str(tmp_path / "dash.db"))
    store.connect()
    for i in range(10):
        store.record_risk("SOL-PERP", float(i), 0.1 * i)
    store.close()


def test_huge_limit_is_capped(tmp_path):
    make_populated_store(tmp_path)
    server, thread = start_server(str(tmp_path / "dash.db"))
    try:
        status, body = get_json(
            server, "/api/series?market=SOL-PERP&limit=999999999"
        )
        assert status == 200
        assert len(body["price"]) <= 5000
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_non_positive_limit_is_rejected(tmp_path):
    make_populated_store(tmp_path)
    server, thread = start_server(str(tmp_path / "dash.db"))
    try:
        status, body = get_json(server, "/api/series?market=SOL-PERP&limit=0")
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        thread.join(timeout=5)
