"""
tests/test_dashboard.py

Tests the dashboard's ``?limit=`` query-param handling. SQLite treats a
negative LIMIT as "no limit" and an unbounded positive value lets a client
force a multi-million-row scan, so the handler must clamp to a sane range
before it ever reaches the store.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src.dashboard.server import _make_handler
from src.storage import SQLiteStore


@pytest.fixture
def dashboard_url(tmp_path):
    db_path = str(tmp_path / "dash.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5, 150.0)
    store.close()

    handler = _make_handler(db_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_negative_limit_rejected(dashboard_url):
    status, body = _get(f"{dashboard_url}/api/series?market=SOL-PERP&limit=-1")
    assert status == 400
    assert "limit" in body["error"]


def test_oversized_limit_rejected(dashboard_url):
    status, body = _get(f"{dashboard_url}/api/series?market=SOL-PERP&limit=999999999")
    assert status == 400
    assert "limit" in body["error"]


def test_zero_limit_rejected(dashboard_url):
    status, _ = _get(f"{dashboard_url}/api/series?market=SOL-PERP&limit=0")
    assert status == 400


def test_reasonable_limit_accepted(dashboard_url):
    status, body = _get(f"{dashboard_url}/api/series?market=SOL-PERP&limit=500")
    assert status == 200
    assert body["risk"] == [{"time": 1, "value": 0.5}]


def test_default_limit_accepted(dashboard_url):
    status, body = _get(f"{dashboard_url}/api/series?market=SOL-PERP")
    assert status == 200
    assert body["price"] == [{"time": 1, "value": 150.0}]
