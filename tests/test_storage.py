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
    store.record_risk("SOL-PERP", 1.0, 0.55, 150.0)
    store.record_risk("SOL-PERP", 2.0, 0.62, 150.5)
    assert store.counts()["risk"] == 2
    store.close()


def test_series_and_markets_readback(tmp_path):
    store = make_store(tmp_path)
    store.record_risk("SOL-PERP", 1.2, 0.5, 150.0)
    store.record_risk("SOL-PERP", 1.8, 0.7, 151.0)  # same second -> averaged
    store.record_risk("SOL-PERP", 2.4, 0.6, 152.0)
    store.record_detections(1.0, [det("flicker", 0.8)])

    assert store.markets() == ["SOL-PERP"]

    price = store.price_series("SOL-PERP")
    risk = store.risk_series("SOL-PERP")
    # Two distinct seconds (1 and 2), ascending and unique.
    assert [p["time"] for p in price] == [1, 2]
    assert price[0]["value"] == 150.5  # avg of 150.0 and 151.0
    assert [r["time"] for r in risk] == [1, 2]

    markers = store.detection_markers("SOL-PERP")
    assert markers[0]["detector"] == "flicker"
    assert markers[0]["time"] == 1
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


def test_record_tick_writes_detections_and_risk_in_one_transaction(tmp_path):
    store = make_store(tmp_path)
    store.record_tick("SOL-PERP", snap(), [det("imbalance", 0.9)], risk=0.5)
    assert store.counts() == {"snapshots": 0, "detections": 1, "risk": 1}
    store.close()


def test_record_tick_persists_snapshot_only_when_requested(tmp_path):
    store = make_store(tmp_path)
    store.record_tick("SOL-PERP", snap(), [], risk=None, persist_snapshot=True)
    assert store.counts()["snapshots"] == 1
    store.close()


def test_record_tick_visible_without_explicit_commit(tmp_path):
    # record_tick batches writes into a single commit; readers on the same
    # connection must still see the rows without the caller calling close().
    store = make_store(tmp_path)
    store.record_tick("SOL-PERP", snap(), [det()], risk=0.4)
    assert store.counts()["detections"] == 1
    assert store.counts()["risk"] == 1
    store.close()


def test_snapshot_markets_lists_only_markets_with_persisted_snapshots(tmp_path):
    store = make_store(tmp_path)
    store.record_risk("SOL-PERP", 1.0, 0.5)  # a market with risk but no snapshot
    store.record_snapshot(snap(market="BTC-PERP"))
    assert store.snapshot_markets() == ["BTC-PERP"]
    store.close()


def test_snapshots_round_trips_bids_and_asks(tmp_path):
    store = make_store(tmp_path)
    store.record_snapshot(snap(ts=1.0))
    store.record_snapshot(snap(ts=2.0))

    rows = list(store.snapshots("SOL-PERP"))
    assert [r.timestamp for r in rows] == [1.0, 2.0]
    assert [(l.price, l.size) for l in rows[0].bids] == [(100.0, 5.0), (99.9, 3.0)]
    assert [(l.price, l.size) for l in rows[0].asks] == [(100.1, 4.0), (100.2, 2.0)]
    store.close()


def test_snapshots_respects_time_range(tmp_path):
    store = make_store(tmp_path)
    store.record_snapshot(snap(ts=1.0))
    store.record_snapshot(snap(ts=2.0))
    store.record_snapshot(snap(ts=3.0))

    rows = list(store.snapshots("SOL-PERP", start_ts=1.5, end_ts=2.5))
    assert [r.timestamp for r in rows] == [2.0]
    store.close()
