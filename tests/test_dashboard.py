"""
tests/test_dashboard.py

Tests the dashboard's HTTP API, in particular the ``limit`` query-param
validation: SQLite's LIMIT clause treats negative values as "no limit" and
raises OverflowError on values outside a 64-bit signed int, so the handler
must reject out-of-range values itself rather than passing them through.
"""

import json
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src.dashboard.server import _make_handler
from src.storage import SQLiteStore


@pytest.fixture
def server(tmp_path):
    db_path = str(tmp_path / "test.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5, 150.0)
    store.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(db_path))
    import threading

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join()


def _get(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_valid_limit_ok(server):
    status, body = _get(f"{server}/api/series?market=SOL-PERP&limit=50")
    assert status == 200
    assert "price" in body and "risk" in body


def test_negative_limit_rejected(server):
    # A negative LIMIT in SQLite means "unbounded" — must be rejected, not passed through.
    status, body = _get(f"{server}/api/series?market=SOL-PERP&limit=-5")
    assert status == 400
    assert "limit" in body["error"]


def test_zero_limit_rejected(server):
    status, _ = _get(f"{server}/api/series?market=SOL-PERP&limit=0")
    assert status == 400


def test_oversized_limit_rejected(server):
    # Larger than SQLite's 64-bit INTEGER range — must not reach the query.
    status, body = _get(f"{server}/api/series?market=SOL-PERP&limit=999999999999999999999999")
    assert status == 400
    assert "limit" in body["error"]


def test_non_integer_limit_rejected(server):
    status, _ = _get(f"{server}/api/series?market=SOL-PERP&limit=abc")
    assert status == 400


def test_missing_market_rejected(server):
    status, _ = _get(f"{server}/api/series?limit=10")
    assert status == 400
