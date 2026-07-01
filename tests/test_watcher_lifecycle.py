"""
tests/test_watcher_lifecycle.py

Regression test: Watcher.start() must close the store even if something
between store.connect() and the run loop (e.g. feed creation) fails or is
interrupted. Previously store.connect() and create_feed() both ran before the
try/finally that closes the store, so a failure there leaked the open SQLite
connection/WAL files. Network-free (storage uses an in-memory DB).
"""

import asyncio

import pytest

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(
        rpc_url="http://localhost", drift_env="devnet", keypair_path="",
        markets=("SOL-PERP",), storage_enabled=True, db_path=":memory:",
    )
    base.update(overrides)
    return Settings(**base)


def test_store_is_closed_when_feed_creation_fails(monkeypatch):
    settings = make_settings()
    watcher = Watcher(settings)

    closed = {"called": False}
    orig_close = watcher.store.close

    def spy_close():
        closed["called"] = True
        orig_close()

    monkeypatch.setattr(watcher.store, "close", spy_close)

    async def failing_create_feed(_settings):
        raise RuntimeError("boom")

    monkeypatch.setattr("src.watcher.create_feed", failing_create_feed)

    async def run():
        with pytest.raises(RuntimeError):
            await watcher.start()

    asyncio.run(run())

    assert closed["called"] is True
    assert watcher.feed is None  # never got assigned; close() must tolerate that
