"""
tests/test_dashboard.py

Regression coverage for the dashboard's `limit` query-param handling.
SQLite treats a non-positive LIMIT as "no limit", so the handler must reject
(or clamp) bad values itself rather than passing them straight through.
"""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from src.dashboard.server import _MAX_LIMIT, _make_handler
from src.storage import SQLiteStore


def _start_server(db_path):
    handler = _make_handler(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server, path):
    port = server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _get_error(server, path):
    port = server.server_address[1]
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
        raise AssertionError("expected HTTPError")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_negative_limit_is_rejected_not_treated_as_unbounded(tmp_path):
    db_path = str(tmp_path / "dash.db")
    store = SQLiteStore(db_path)
    store.connect()
    for i in range(10):
        store.record_risk("SOL-PERP", float(i), 0.5)
    store.close()

    server, thread = _start_server(db_path)
    try:
        status, body = _get_error(server, "/api/series?market=SOL-PERP&limit=-1")
        assert status == 400
        assert "error" in body
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_limit_above_max_is_clamped(tmp_path):
    db_path = str(tmp_path / "dash2.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5)
    store.close()

    server, thread = _start_server(db_path)
    try:
        status, body = _get(
            server, f"/api/series?market=SOL-PERP&limit={_MAX_LIMIT * 10}"
        )
        assert status == 200
        assert "price" in body and "risk" in body
    finally:
        server.shutdown()
        thread.join(timeout=5)
