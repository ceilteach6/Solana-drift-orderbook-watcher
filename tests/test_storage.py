"""
tests/test_storage.py

Tests the SQLite time-series store: schema creation, writes, and read-back.
Uses a temporary on-disk DB so WAL mode behaves as in production.
"""

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector.base import Detection
from src.storage import SQLiteStore


def make_store(tmp_path):
    store = SQLiteStore(str(tmp_path / "test.db"))
    store.connect()
    return store


def snap(market="SOL-PERP", ts=1.0):
    return OrderbookSnapshot(
        market=market,
        timestamp=ts,
        bids=[Level(100.0, 5.0), Level(99.9, 3.0)],
        asks=[Level(100.1, 4.0), Level(100.2, 2.0)],
    )


def det(detector="imbalance", score=0.9, market="SOL-PERP"):
    return Detection(detector=detector, market=market, score=score,
                     message="x", details={"k": "v"})


def test_schema_and_counts_start_empty(tmp_path):
    store = make_store(tmp_path)
    assert store.counts() == {"snapshots": 0, "detections": 0, "risk": 0}
    store.close()


def test_record_snapshot(tmp_path):
    store = make_store(tmp_path)
    store.record_snapshot(snap())
    assert store.counts()["snapshots"] == 1
    store.close()


def test_record_detections_and_readback(tmp_path):
    store = make_store(tmp_path)
    store.record_detections(1.0, [det("imbalance", 0.9), det("flicker", 0.7)])
    assert store.counts()["detections"] == 2
    recent = store.recent_detections(10)
    detectors = {r["detector"] for r in recent}
    assert detectors == {"imbalance", "flicker"}
    assert all(r["ts"] == 1.0 for r in recent)
    store.close()


def test_record_detections_empty_is_noop(tmp_path):
    store = make_store(tmp_path)
    store.record_detections(1.0, [])
    assert store.counts()["detections"] == 0
    store.close()


def test_record_risk(tmp_path):
    store = make_store(tmp_path)
    store.record_risk("SOL-PERP", 1.0, 0.55)
    store.record_risk("SOL-PERP", 2.0, 0.62)
    assert store.counts()["risk"] == 2
    store.close()


def test_summary_runs(tmp_path):
    store = make_store(tmp_path)
    store.record_detections(1.0, [det()])
    text = store.summary()
    assert "Storage summary" in text
    assert "detections" in text
    store.close()


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "persist.db")
    store = SQLiteStore(path)
    store.connect()
    store.record_risk("SOL-PERP", 1.0, 0.5)
    store.close()

    reopened = SQLiteStore(path)
    reopened.connect()
    assert reopened.counts()["risk"] == 1
    reopened.close()
