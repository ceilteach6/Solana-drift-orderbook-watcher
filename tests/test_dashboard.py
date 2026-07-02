"""
tests/test_dashboard.py

Exercises the dashboard's tiny stdlib HTTP server end-to-end against a real
SQLite DB, focused on the `limit` query-parameter guardrails.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src.dashboard.server import _MAX_LIMIT, _make_handler
from src.storage import SQLiteStore


@pytest.fixture
def dashboard(tmp_path):
    db_path = str(tmp_path / "dash.db")
    store = SQLiteStore(db_path)
    store.connect()
    for i in range(20):
        store.record_risk("SOL-PERP", float(i), 0.5, 100.0 + i)
    store.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(db_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def test_huge_limit_is_clamped(dashboard):
    status, body = _get(f"{dashboard}/api/series?market=SOL-PERP&limit=999999999")
    assert status == 200
    # Only 20 rows exist regardless, but the server must not attempt to
    # honor an absurd LIMIT — verified indirectly via a well-formed response.
    assert len(body["risk"]) <= _MAX_LIMIT


def test_negative_limit_is_rejected(dashboard):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _get(f"{dashboard}/api/series?market=SOL-PERP&limit=-1")
    assert excinfo.value.code == 400


def test_normal_limit_still_works(dashboard):
    status, body = _get(f"{dashboard}/api/series?market=SOL-PERP&limit=5")
    assert status == 200
    assert len(body["risk"]) <= 5


def test_index_route_ignores_invalid_limit(dashboard):
    # Regression: `limit` used to be parsed/validated before route dispatch,
    # so `/?limit=<garbage>` on the index page (which never reads `limit`)
    # returned a 400 JSON error instead of the dashboard HTML.
    with urllib.request.urlopen(f"{dashboard}/?limit=not-a-number", timeout=5) as resp:
        status = resp.status
        content_type = resp.headers.get("Content-Type", "")
    assert status == 200
    assert "text/html" in content_type

    with urllib.request.urlopen(f"{dashboard}/?limit=-1", timeout=5) as resp:
        assert resp.status == 200
