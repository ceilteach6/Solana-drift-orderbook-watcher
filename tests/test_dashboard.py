"""
tests/test_dashboard.py

Exercises the dashboard's HTTP handler end-to-end (real socket, real
sqlite3.Connection) rather than testing SQLiteStore in isolation — this is
the layer that previously had zero coverage, which is how the unbounded
`limit` query param went unnoticed (see src/dashboard/server.py).
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from src.dashboard.server import _MAX_LIMIT, _make_handler
from src.detector.base import Detection
from src.storage import SQLiteStore


@pytest.fixture
def dashboard(tmp_path):
    store = SQLiteStore(str(tmp_path / "dash.db"))
    store.connect(check_same_thread=False)
    for i in range(10):
        store.record_risk("SOL-PERP", float(i), 0.1 * i, 100.0 + i)
    store.record_detections(5.0, [
        Detection(detector="flicker", market="SOL-PERP", score=0.8,
                  message="x", details={}),
    ])

    handler = _make_handler(store, threading.Lock())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def test_markets_endpoint(dashboard):
    status, body = _get(f"{dashboard}/api/markets")
    assert status == 200
    assert body == ["SOL-PERP"]


def test_series_endpoint(dashboard):
    status, body = _get(f"{dashboard}/api/series?market=SOL-PERP")
    assert status == 200
    assert len(body["price"]) == 10
    assert len(body["risk"]) == 10


def test_series_requires_market(dashboard):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{dashboard}/api/series", timeout=5)
    assert exc_info.value.code == 400


def test_detections_endpoint(dashboard):
    status, body = _get(f"{dashboard}/api/detections?market=SOL-PERP")
    assert status == 200
    assert body[0]["detector"] == "flicker"


def test_limit_is_clamped_not_unbounded(dashboard):
    # A negative limit would make SQLite return every row unbounded
    # (`LIMIT -1` means "no limit"); it must be clamped to the minimum instead.
    status, body = _get(f"{dashboard}/api/series?market=SOL-PERP&limit=-1")
    assert status == 200
    assert len(body["price"]) == 1

    status, body = _get(f"{dashboard}/api/series?market=SOL-PERP&limit=999999999")
    assert status == 200
    assert len(body["price"]) <= _MAX_LIMIT


def test_limit_must_be_integer(dashboard):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{dashboard}/api/series?market=SOL-PERP&limit=abc", timeout=5)
    assert exc_info.value.code == 400


def test_unknown_route_is_404(dashboard):
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(f"{dashboard}/api/nope", timeout=5)
    assert exc_info.value.code == 404
