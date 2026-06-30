"""
tests/test_replay.py

Unit tests for offline replay (src/replay.py): persisted L2 snapshots must
round-trip through SQLite and re-fire the same detectors they would live.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay_market
from src.storage import SQLiteStore


def make_settings(db_path, **overrides):
    base = dict(
        db_path=db_path,
        update_frequency_ms=1000,
        repeated_min_count=4,
        repeated_size_tolerance=0.001,
        layering_min_levels=5,
        flicker_window_sec=5.0,
        flicker_min_events=3,
        imbalance_min_ratio=0.85,
        imbalance_min_levels=5,
        spoof_window_sec=10.0,
        spoof_wall_ratio=5.0,
        spoof_min_price_move=0.001,
        spoof_pull_fraction=0.5,
        risk_aggregation=True,
        risk_smoothing=0.4,
        risk_alert_threshold=0.6,
        risk_clear_threshold=0.4,
        risk_alert_cooldown_sec=30.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def snap(market, ts, bids, asks):
    return OrderbookSnapshot(
        market=market,
        timestamp=ts,
        bids=[Level(p, s) for p, s in bids],
        asks=[Level(p, s) for p, s in asks],
    )


def test_replay_with_no_persisted_snapshots_is_empty(tmp_path):
    settings = make_settings(str(tmp_path / "watcher.db"))
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.close()

    result = replay_market(settings, "SOL-PERP")
    assert result.snapshots == 0
    assert result.detections == {}
    assert result.risk_alerts == 0


def test_replay_refires_repeated_size_detector(tmp_path):
    settings = make_settings(str(tmp_path / "watcher.db"))
    store = SQLiteStore(settings.db_path)
    store.connect()
    # 4 bids share the exact same size -> repeated_size signature.
    store.record_snapshot(snap(
        "SOL-PERP", 1.0,
        bids=[(100.0, 10.0), (99.9, 10.0), (99.8, 10.0), (99.7, 10.0)],
        asks=[(100.1, 1.0), (100.2, 2.0)],
    ))
    store.close()

    result = replay_market(settings, "SOL-PERP")
    assert result.snapshots == 1
    assert result.detections.get("repeated_size", 0) >= 1


def test_replay_is_isolated_per_market(tmp_path):
    settings = make_settings(str(tmp_path / "watcher.db"))
    store = SQLiteStore(settings.db_path)
    store.connect()
    store.record_snapshot(snap(
        "SOL-PERP", 1.0,
        bids=[(100.0, 10.0), (99.9, 10.0), (99.8, 10.0), (99.7, 10.0)],
        asks=[(100.1, 1.0)],
    ))
    store.record_snapshot(snap(
        "BTC-PERP", 1.0,
        bids=[(50000.0, 1.0), (49990.0, 2.0)],
        asks=[(50010.0, 1.0)],
    ))
    store.close()

    result = replay_market(settings, "BTC-PERP")
    assert result.snapshots == 1
    assert "repeated_size" not in result.detections
