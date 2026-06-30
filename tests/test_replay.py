"""
tests/test_replay.py

The replay harness re-runs the live detector stack over snapshots persisted
by SQLiteStore.record_snapshot. Verify the round trip (DB write -> JSON ->
Level/OrderbookSnapshot reconstruction -> detector) actually fires on a
known-positive pattern, and that replay never mutates the database.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay_market, run_replay
from src.storage import SQLiteStore


def make_settings(**overrides):
    base = dict(
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
        update_frequency_ms=1000,
        risk_aggregation=False,
        risk_smoothing=0.4,
        risk_alert_threshold=0.6,
        risk_clear_threshold=0.4,
        risk_alert_cooldown_sec=30.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def store_with_imbalance_snapshot(tmp_path):
    """One snapshot with a heavily bid-stacked book -> imbalance detector fires."""
    store = SQLiteStore(str(tmp_path / "replay.db"))
    store.connect()
    snapshot = OrderbookSnapshot(
        market="SOL-PERP",
        timestamp=1000.0,
        bids=[Level(100.0 - i, 100.0) for i in range(5)],
        asks=[Level(101.0 + i, 1.0) for i in range(5)],
    )
    store.record_snapshot(snapshot)
    store.close()
    return store


def test_replay_market_reconstructs_snapshots_and_fires_detector(tmp_path):
    store = store_with_imbalance_snapshot(tmp_path)
    store.connect()
    settings = make_settings()
    try:
        result = replay_market(settings, store, "SOL-PERP")
    finally:
        store.close()

    assert result.snapshots == 1
    assert result.detector_stats["imbalance"].count == 1
    assert result.detector_stats["imbalance"].score_max > 0


def test_replay_market_unknown_market_is_empty(tmp_path):
    store = store_with_imbalance_snapshot(tmp_path)
    store.connect()
    settings = make_settings()
    try:
        result = replay_market(settings, store, "NOPE-PERP")
    finally:
        store.close()

    assert result.snapshots == 0
    assert all(s.count == 0 for s in result.detector_stats.values())


def test_replay_does_not_mutate_database(tmp_path):
    store = store_with_imbalance_snapshot(tmp_path)
    store.connect()
    before = store.counts()
    settings = make_settings()
    replay_market(settings, store, "SOL-PERP")
    after = store.counts()
    store.close()

    assert before == after


def test_run_replay_without_db_returns_error_exit_code(tmp_path):
    settings = make_settings(db_path=str(tmp_path / "missing.db"))
    assert run_replay(settings) == 1


def test_run_replay_without_snapshots_returns_error_exit_code(tmp_path):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    settings = make_settings(db_path=db_path)
    assert run_replay(settings) == 1


def test_run_replay_with_market_filter_succeeds(tmp_path):
    store = store_with_imbalance_snapshot(tmp_path)
    settings = make_settings(db_path=store.db_path)
    assert run_replay(settings, market="SOL-PERP") == 0
