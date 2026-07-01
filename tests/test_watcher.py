"""
tests/test_watcher.py

Regression: Watcher.start() must clean up whichever resources it actually
acquired even if a later setup step fails. Previously the SQLite store was
connected before the try/finally began, so a feed-connection failure left the
store's connection (and, on disk, its WAL/SHM files and file lock) open for
the life of the crashed process.
"""

from types import SimpleNamespace

import pytest

import src.watcher as watcher_mod
from src.watcher import Watcher


class _FakeStore:
    def __init__(self, path):
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True


def make_settings(**overrides):
    base = dict(
        markets=["SOL-PERP"],
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        risk_aggregation=False,
        storage_enabled=True,
        db_path=":memory:",
        healthcheck_enabled=False,
        healthcheck_interval_sec=300.0,
        alert_min_score=0.6,
        alert_format="console",
        alert_webhook_url="",
        telegram_bot_token="",
        telegram_chat_id="",
        run_duration_sec=0.0,
        persist_snapshots=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_store_closed_when_feed_setup_fails(monkeypatch):
    fake_store = _FakeStore(":memory:")
    monkeypatch.setattr(watcher_mod, "SQLiteStore", lambda path: fake_store)

    async def boom(settings):
        raise RuntimeError("feed init failed")

    monkeypatch.setattr(watcher_mod, "create_feed", boom)

    watcher = Watcher(make_settings())
    with pytest.raises(RuntimeError):
        await watcher.start()

    assert fake_store.connected
    assert fake_store.closed
