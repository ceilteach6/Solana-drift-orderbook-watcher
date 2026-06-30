"""
tests/test_watcher.py

Regression test: if ``create_feed()`` raises before ``self.feed`` is ever
assigned, ``Watcher.start()`` must propagate the original error — not mask it
with an ``AttributeError`` from calling ``.close()`` on ``None`` in the
``finally`` block — and it must still close the storage connection it had
already opened.
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.watcher as watcher_mod
from src.watcher import Watcher


def make_settings(tmp_path, **overrides):
    base = dict(
        markets=("SOL-PERP",),
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
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
        risk_aggregation=False,
        storage_enabled=True,
        db_path=str(tmp_path / "watcher.db"),
        persist_snapshots=False,
        alert_min_score=0.6,
        alert_format="console",
        alert_webhook_url="",
        telegram_bot_token="",
        telegram_chat_id="",
        healthcheck_enabled=False,
        healthcheck_interval_sec=300.0,
        run_duration_sec=0.001,  # stop almost immediately if the loop is ever entered
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_start_propagates_feed_error_and_closes_store(tmp_path, monkeypatch):
    settings = make_settings(tmp_path)
    watcher = Watcher(settings)

    async def boom(_settings):
        raise RuntimeError("feed unavailable")

    monkeypatch.setattr(watcher_mod, "create_feed", boom)

    with pytest.raises(RuntimeError, match="feed unavailable"):
        asyncio.run(watcher.start())

    assert watcher.feed is None
    # store.close() sets _conn back to None; reaching that line proves close()
    # ran instead of being skipped by an AttributeError on self.feed.close().
    assert watcher.store._conn is None
