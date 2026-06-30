"""
tests/test_watcher.py

Tests for Watcher.start()'s resource lifecycle, using a fake feed (no
network/driftpy) and a fake store:

- A storage init failure must degrade (persistence disabled) instead of
  crashing the whole watcher.
- The feed and the store must both be closed on the way out, even when feed
  creation fails after the store connected successfully (no leaked store
  handle on a half-started watcher).
"""

import asyncio

import pytest

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    kwargs = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
        update_frequency_ms=10,
        run_duration_sec=0.03,
        risk_aggregation=False,
        healthcheck_enabled=False,
        storage_enabled=False,
    )
    kwargs.update(overrides)
    return Settings(**kwargs)


class FakeFeed:
    def __init__(self):
        self.closed = False

    async def get_snapshot(self, market):
        return None

    async def close(self):
        self.closed = True


class RaisingStore:
    """Stands in for SQLiteStore when its connect() should fail."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.closed = False

    def connect(self):
        raise PermissionError("simulated: cannot create db directory")

    def close(self):  # pragma: no cover - must never be reached
        self.closed = True


class FakeStore:
    def __init__(self, db_path):
        self.db_path = db_path
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True


def test_storage_init_failure_degrades_instead_of_crashing(monkeypatch):
    settings = make_settings(storage_enabled=True, db_path="unused")
    watcher = Watcher(settings)
    watcher.store = RaisingStore(settings.db_path)

    fake_feed = FakeFeed()
    monkeypatch.setattr("src.watcher.create_feed", _async_returning(fake_feed))

    asyncio.run(watcher.start())

    assert watcher.store is None  # disabled, not propagated as a crash
    assert fake_feed.closed


def test_feed_and_store_closed_on_clean_run(monkeypatch):
    settings = make_settings(storage_enabled=True, db_path="unused")
    watcher = Watcher(settings)
    fake_store = FakeStore(settings.db_path)
    watcher.store = fake_store

    fake_feed = FakeFeed()
    monkeypatch.setattr("src.watcher.create_feed", _async_returning(fake_feed))

    asyncio.run(watcher.start())

    assert fake_store.connected
    assert fake_store.closed
    assert fake_feed.closed


def test_store_still_closed_when_feed_creation_fails(monkeypatch):
    """If create_feed() raises after the store connected, the store must not
    be leaked open just because the feed never came up."""
    settings = make_settings(storage_enabled=True, db_path="unused")
    watcher = Watcher(settings)
    fake_store = FakeStore(settings.db_path)
    watcher.store = fake_store

    async def failing_create_feed(_settings):
        raise RuntimeError("simulated feed creation failure")

    monkeypatch.setattr("src.watcher.create_feed", failing_create_feed)

    with pytest.raises(RuntimeError, match="simulated feed creation failure"):
        asyncio.run(watcher.start())

    assert fake_store.connected
    assert fake_store.closed


def _async_returning(value):
    async def _fn(_settings):
        return value

    return _fn
