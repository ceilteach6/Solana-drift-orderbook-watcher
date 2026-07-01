"""
tests/test_replay.py

Tests the replay/backtest pipeline: it must reconstruct persisted snapshots
faithfully and run them through a fresh detector + risk stack (independent of
any live watcher state).
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.replay import replay
from src.storage import SQLiteStore


def make_settings(db_path, **overrides):
    base = dict(
        markets=["SOL-PERP"],
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
        risk_aggregation=False,
        risk_smoothing=0.4,
        risk_alert_threshold=0.6,
        risk_clear_threshold=0.4,
        risk_alert_cooldown_sec=30.0,
        alert_min_score=0.6,
        alert_format="console",
        alert_webhook_url="",
        telegram_bot_token="",
        telegram_chat_id="",
        db_path=db_path,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _persist_imbalanced_ticks(db_path, market="SOL-PERP", count=5):
    store = SQLiteStore(db_path)
    store.connect()
    bids = [(100.0 - i, 100.0) for i in range(5)]
    asks = [(101.0 + i, 1.0) for i in range(5)]
    for i in range(count):
        store.record_snapshot(
            OrderbookSnapshot(
                market=market,
                timestamp=float(i),
                bids=[Level(p, s) for p, s in bids],
                asks=[Level(p, s) for p, s in asks],
            )
        )
    store.close()


def test_replay_reprocesses_persisted_snapshots(tmp_path):
    db_path = str(tmp_path / "test.db")
    _persist_imbalanced_ticks(db_path, count=5)

    settings = make_settings(db_path)
    stats = asyncio.run(replay(settings, "SOL-PERP"))

    assert stats.ticks == 5
    assert stats.by_detector.get("imbalance", 0) == 5  # fires on every tick


def test_replay_raises_for_market_with_no_snapshots(tmp_path):
    db_path = str(tmp_path / "empty.db")
    store = SQLiteStore(db_path)
    store.connect()
    store.close()

    settings = make_settings(db_path)
    with pytest.raises(ValueError):
        asyncio.run(replay(settings, "SOL-PERP"))


def test_replay_feeds_risk_aggregator_when_enabled(tmp_path):
    db_path = str(tmp_path / "test.db")
    _persist_imbalanced_ticks(db_path, count=8)

    settings = make_settings(db_path, risk_aggregation=True, risk_smoothing=1.0)
    stats = asyncio.run(replay(settings, "SOL-PERP"))

    assert stats.ticks == 8
    assert stats.alerts >= 1  # sustained imbalance should raise a risk alert
