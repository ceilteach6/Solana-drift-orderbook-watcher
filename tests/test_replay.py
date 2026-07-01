"""
tests/test_replay.py

Replay must reproduce what the live watcher would have detected from the
same persisted L2 history — same detector stack, same history window sizing.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay_market
from src.storage import SQLiteStore


def make_settings(db_path, **overrides):
    base = dict(
        db_path=db_path,
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        risk_aggregation=False,
        repeated_min_count=4,
        repeated_size_tolerance=0.001,
        layering_min_levels=5,
        flicker_min_events=3,
        imbalance_min_ratio=0.85,
        imbalance_min_levels=5,
        spoof_window_sec=10.0,
        spoof_wall_ratio=5.0,
        spoof_min_price_move=0.001,
        spoof_pull_fraction=0.5,
        alert_min_score=0.6,
        alert_format="console",
        alert_webhook_url="",
        telegram_bot_token="",
        telegram_chat_id="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _record_repeated_size_scenario(store, market="SOL-PERP"):
    # 4 bid levels share the same size -> the repeated_size detector fires.
    snapshot = OrderbookSnapshot(
        market=market,
        timestamp=1.0,
        bids=[Level(100 - i, 10.0) for i in range(4)],
        asks=[Level(101, 1.0)],
    )
    store.record_snapshot(snapshot)


def test_replay_market_counts_snapshots_and_detections(tmp_path, capsys):
    db_path = str(tmp_path / "replay.db")
    store = SQLiteStore(db_path)
    store.connect()
    _record_repeated_size_scenario(store)
    store.close()

    settings = make_settings(db_path)
    count = replay_market(settings, "SOL-PERP")

    assert count == 1
    out = capsys.readouterr().out
    assert "Replay of SOL-PERP: 1 snapshots" in out
    assert "repeated_size" in out


def test_replay_market_returns_zero_for_unknown_market(tmp_path):
    db_path = str(tmp_path / "replay.db")
    store = SQLiteStore(db_path)
    store.connect()
    _record_repeated_size_scenario(store, market="SOL-PERP")
    store.close()

    settings = make_settings(db_path)
    assert replay_market(settings, "BTC-PERP") == 0
