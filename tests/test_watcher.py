"""
tests/test_watcher.py

Unit tests for Watcher's resilience plumbing: a hung snapshot fetch must be
aborted by the timeout (not stall the loop forever), repeated failures must
trigger a feed reconnect, a successful tick must reset the failure counter,
and a broken health-check self-test must not take the whole process down.
Network-free — fakes the feed and patches create_feed/run_selftest.
"""

import asyncio
import dataclasses

import pytest

from config.settings import load_settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.watcher import Watcher


def make_settings(**overrides):
    base = load_settings()
    overrides.setdefault("markets", ["SOL-PERP"])
    overrides.setdefault("storage_enabled", False)
    return dataclasses.replace(base, **overrides)


class FailingFeed:
    def __init__(self):
        self.closed = False

    async def get_snapshot(self, market):
        raise RuntimeError("boom")

    async def close(self):
        self.closed = True


class HangingFeed:
    async def get_snapshot(self, market):
        await asyncio.sleep(10)

    async def close(self):
        pass


class GoodFeed:
    async def get_snapshot(self, market):
        return OrderbookSnapshot(
            market=market, timestamp=0.0,
            bids=[Level(100.0, 1.0)], asks=[Level(101.0, 1.0)],
        )

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_hung_snapshot_is_aborted_by_timeout():
    settings = make_settings(snapshot_timeout_sec=0.05, feed_reconnect_after_failures=100)
    watcher = Watcher(settings)
    watcher.feed = HangingFeed()

    await asyncio.wait_for(watcher._tick("SOL-PERP"), timeout=1.0)
    assert watcher._consecutive_failures == 1


@pytest.mark.asyncio
async def test_reconnects_after_consecutive_failure_threshold(monkeypatch):
    settings = make_settings(snapshot_timeout_sec=1.0, feed_reconnect_after_failures=3)
    watcher = Watcher(settings)
    old_feed = FailingFeed()
    watcher.feed = old_feed

    new_feed = GoodFeed()

    async def fake_create_feed(_settings):
        return new_feed

    monkeypatch.setattr("src.watcher.create_feed", fake_create_feed)

    for _ in range(3):
        await watcher._tick("SOL-PERP")

    assert old_feed.closed is True
    assert watcher.feed is new_feed
    assert watcher._consecutive_failures == 0


@pytest.mark.asyncio
async def test_successful_tick_resets_failure_counter():
    watcher = Watcher(make_settings())
    watcher._consecutive_failures = 4
    watcher.feed = GoodFeed()

    await watcher._tick("SOL-PERP")

    assert watcher._consecutive_failures == 0


def test_healthcheck_exception_does_not_propagate(monkeypatch):
    settings = make_settings(healthcheck_enabled=True, healthcheck_interval_sec=0.0)
    watcher = Watcher(settings)

    def boom(_settings):
        raise RuntimeError("selftest exploded")

    monkeypatch.setattr("src.watcher.run_selftest", boom)

    watcher._maybe_healthcheck()  # must not raise
