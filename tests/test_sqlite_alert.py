"""
tests/test_sqlite_alert.py

Integration tests for the SqliteAlert sink.
Uses a temporary in-memory SQLite database — no filesystem side-effects.

Run:
    pytest tests/test_sqlite_alert.py -v
"""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.alert.sqlite_alert import SqliteAlert
from src.detector.base import Detection


def make_settings(path: str = ":memory:") -> SimpleNamespace:
    return SimpleNamespace(sqlite_path=path, alert_min_score=0.0)


def make_detection(**overrides) -> Detection:
    base = dict(
        detector="repeated_size",
        market="SOL-PERP",
        score=0.8,
        message="Test detection",
        details={"count": 5, "size": 10.0},
    )
    base.update(overrides)
    return Detection(**base)


# --------------------------------------------------------------------------- #
# is_configured guard
# --------------------------------------------------------------------------- #
def test_not_configured_when_empty_path():
    assert not SqliteAlert.is_configured(SimpleNamespace(sqlite_path=""))


def test_configured_when_path_set():
    assert SqliteAlert.is_configured(SimpleNamespace(sqlite_path=":memory:"))


# --------------------------------------------------------------------------- #
# Schema creation
# --------------------------------------------------------------------------- #
def test_table_created_on_init():
    sink = SqliteAlert(make_settings())
    assert sink._conn is not None
    cur = sink._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "detections" in tables


# --------------------------------------------------------------------------- #
# Delivery
# --------------------------------------------------------------------------- #
def test_deliver_writes_row():
    sink = SqliteAlert(make_settings())
    d = make_detection()
    asyncio.run(sink.deliver(d))

    row = sink._conn.execute("SELECT * FROM detections").fetchone()
    assert row is not None
    _, ts, market, detector, score, message, details_json = row
    assert market == "SOL-PERP"
    assert detector == "repeated_size"
    assert score == pytest.approx(0.8, abs=1e-5)
    assert message == "Test detection"
    assert json.loads(details_json) == {"count": 5, "size": 10.0}


def test_deliver_multiple_rows():
    sink = SqliteAlert(make_settings())
    for i in range(5):
        asyncio.run(sink.deliver(make_detection(score=0.5 + i * 0.1)))

    count = sink._conn.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
    assert count == 5


def test_deliver_different_detectors():
    sink = SqliteAlert(make_settings())
    for det_name in ("repeated_size", "layering", "flicker", "imbalance", "volume_spike"):
        asyncio.run(sink.deliver(make_detection(detector=det_name)))

    rows = sink._conn.execute("SELECT detector FROM detections").fetchall()
    detectors = {r[0] for r in rows}
    assert detectors == {"repeated_size", "layering", "flicker", "imbalance", "volume_spike"}


# --------------------------------------------------------------------------- #
# Details round-trip
# --------------------------------------------------------------------------- #
def test_details_json_round_trip():
    sink = SqliteAlert(make_settings())
    details = {"nested": {"a": 1, "b": [2, 3]}, "score": 0.99}
    asyncio.run(sink.deliver(make_detection(details=details)))

    raw = sink._conn.execute("SELECT details FROM detections").fetchone()[0]
    assert json.loads(raw) == details


# --------------------------------------------------------------------------- #
# Close
# --------------------------------------------------------------------------- #
def test_close_is_idempotent():
    sink = SqliteAlert(make_settings())
    sink.close()
    sink.close()  # second call must not raise
    assert sink._conn is None


def test_deliver_after_close_is_silent():
    sink = SqliteAlert(make_settings())
    sink.close()
    # Should not raise; conn is None, method returns early.
    asyncio.run(sink.deliver(make_detection()))
