"""
tests/test_storage.py

Unit tests for SqliteStore. No RPC, no driftpy required.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from src.storage.sqlite_store import SqliteStore


def make_detection(**kw):
    return SimpleNamespace(
        market=kw.get("market", "SOL-PERP"),
        detector=kw.get("detector", "test_detector"),
        score=kw.get("score", 0.75),
        message=kw.get("message", "unit test detection"),
        details=kw.get("details", {"key": "value"}),
    )


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(str(tmp_path / "test.db"))
    s.open()
    yield s
    s.close()


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

def test_disabled_store_is_noop():
    """Empty db_path → save and query are silent no-ops."""
    s = SqliteStore("")
    s.open()
    s.save_detection(make_detection())
    assert s.count() == 0
    assert s.query_recent() == []
    s.close()


def test_double_close_is_safe(store):
    store.close()
    store.close()  # must not raise


def test_reopen_preserves_data(tmp_path):
    path = str(tmp_path / "persist.db")
    s = SqliteStore(path)
    s.open()
    s.save_detection(make_detection())
    s.close()

    s2 = SqliteStore(path)
    s2.open()
    rows = s2.query_recent()
    assert len(rows) == 1
    assert rows[0]["detector"] == "test_detector"
    s2.close()


# --------------------------------------------------------------------------- #
# Writes
# --------------------------------------------------------------------------- #

def test_save_single_detection(store):
    store.save_detection(make_detection())
    assert store.count() == 1


def test_save_batch(store):
    dets = [make_detection(score=0.5 + i * 0.1) for i in range(5)]
    store.save_detections(dets)
    assert store.count() == 5


def test_details_roundtrip(store):
    det = make_detection(details={"nested": {"x": 1}, "list": [1, 2, 3]})
    store.save_detection(det)
    row = store.query_recent()[0]
    assert row["details"]["nested"]["x"] == 1
    assert row["details"]["list"] == [1, 2, 3]


def test_score_stored_as_float(store):
    store.save_detection(make_detection(score=0.123456789))
    row = store.query_recent()[0]
    assert row["score"] == pytest.approx(0.123456789)


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #

def test_query_returns_newest_first(store):
    for i in range(3):
        store.save_detection(make_detection(score=float(i)))
    rows = store.query_recent()
    # IDs should be descending (newest first) after ORDER BY ts DESC
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True)


def test_query_by_market(store):
    store.save_detection(make_detection(market="SOL-PERP"))
    store.save_detection(make_detection(market="BTC-PERP"))
    rows = store.query_recent(market="SOL-PERP")
    assert len(rows) == 1
    assert rows[0]["market"] == "SOL-PERP"


def test_query_by_detector(store):
    store.save_detection(make_detection(detector="flicker"))
    store.save_detection(make_detection(detector="layering"))
    rows = store.query_recent(detector="flicker")
    assert len(rows) == 1
    assert rows[0]["detector"] == "flicker"


def test_query_by_min_score(store):
    store.save_detection(make_detection(score=0.4))
    store.save_detection(make_detection(score=0.8))
    rows = store.query_recent(min_score=0.6)
    assert len(rows) == 1
    assert rows[0]["score"] >= 0.6


def test_query_limit(store):
    for _ in range(10):
        store.save_detection(make_detection())
    rows = store.query_recent(limit=3)
    assert len(rows) == 3


def test_count_all(store):
    for _ in range(7):
        store.save_detection(make_detection())
    assert store.count() == 7


def test_count_by_market(store):
    for _ in range(4):
        store.save_detection(make_detection(market="SOL-PERP"))
    for _ in range(3):
        store.save_detection(make_detection(market="BTC-PERP"))
    assert store.count(market="SOL-PERP") == 4
    assert store.count(market="BTC-PERP") == 3


# --------------------------------------------------------------------------- #
# Async API
# --------------------------------------------------------------------------- #

def test_async_save_detection(tmp_path):
    s = SqliteStore(str(tmp_path / "async.db"))
    s.open()

    async def run():
        await s.async_save_detection(make_detection())
        await s.async_save_detections([make_detection(), make_detection()])

    asyncio.run(run())
    assert s.count() == 3
    s.close()


def test_async_noop_on_disabled_store():
    s = SqliteStore("")

    async def run():
        await s.async_save_detection(make_detection())

    asyncio.run(run())  # must not raise


def test_batch_save_non_serializable_details_does_not_raise(store):
    """save_detections must use _safe_json — not raw json.dumps — on every item."""
    det = make_detection(details={"obj": object()})
    store.save_detections([det])  # must not raise TypeError
    assert store.count() == 1
