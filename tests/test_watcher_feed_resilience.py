"""
tests/test_watcher_feed_resilience.py

The watcher must not get stuck on a dead feed:
- if a market's snapshot poll fails repeatedly in a row, the feed is rebuilt
  instead of being polled forever;
- if the watcher is running on the synthetic fallback, it periodically retries
  the real Drift feed instead of staying on fake data for the whole run.
"""

import pytest

import src.watcher as watcher_module
from config.settings import Settings
from src.collector.orderbook_feed import OrderbookFeed, SyntheticOrderbookFeed
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=["SOL-PERP"],
        storage_enabled=False,
        healthcheck_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


class FakeFeed(OrderbookFeed):
    def __init__(self, label):
        self.label = label
        self.closed = False

    async def get_snapshot(self, market):
        return None

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_reconnects_after_consecutive_failures(monkeypatch):
    w = Watcher(make_settings())
    old_feed = FakeFeed("old")

    async def failing_get_snapshot(market):
        raise RuntimeError("subscription is dead")

    old_feed.get_snapshot = failing_get_snapshot
    w.feed = old_feed

    new_feed = FakeFeed("new")
    calls = 0

    async def fake_create_feed(settings):
        nonlocal calls
        calls += 1
        return new_feed

    monkeypatch.setattr(watcher_module, "create_feed", fake_create_feed)

    threshold = watcher_module._MAX_CONSECUTIVE_FEED_FAILURES
    for _ in range(threshold - 1):
        await w._tick("SOL-PERP")
    assert calls == 0, "should not reconnect before the failure threshold"
    assert w.feed is old_feed

    await w._tick("SOL-PERP")  # the failure that crosses the threshold
    assert calls == 1
    assert w.feed is new_feed
    assert old_feed.closed is True
    assert w._consecutive_failures == {}


@pytest.mark.asyncio
async def test_success_resets_the_failure_counter(monkeypatch):
    w = Watcher(make_settings())
    feed = FakeFeed("feed")
    calls = 0

    async def failing_get_snapshot(market):
        raise RuntimeError("blip")

    feed.get_snapshot = failing_get_snapshot
    w.feed = feed

    async def fake_create_feed(settings):
        nonlocal calls
        calls += 1
        return FakeFeed("new")

    monkeypatch.setattr(watcher_module, "create_feed", fake_create_feed)

    # A handful of failures, then a success, should not trip the threshold
    # even after further failures below it.
    for _ in range(watcher_module._MAX_CONSECUTIVE_FEED_FAILURES - 1):
        await w._tick("SOL-PERP")

    async def ok_get_snapshot(market):
        return None

    feed.get_snapshot = ok_get_snapshot
    await w._tick("SOL-PERP")
    assert w._consecutive_failures["SOL-PERP"] == 0

    feed.get_snapshot = failing_get_snapshot
    for _ in range(watcher_module._MAX_CONSECUTIVE_FEED_FAILURES - 1):
        await w._tick("SOL-PERP")
    assert calls == 0, "counter should have been reset by the intervening success"


@pytest.mark.asyncio
async def test_retries_real_feed_while_on_synthetic(monkeypatch):
    settings = make_settings()
    w = Watcher(settings)
    synthetic = SyntheticOrderbookFeed(settings)
    await synthetic.connect()
    w.feed = synthetic
    w._last_feed_upgrade_attempt = 0.0

    real_feed = FakeFeed("real")

    async def fake_create_feed(settings):
        return real_feed

    monkeypatch.setattr(watcher_module, "create_feed", fake_create_feed)

    await w._maybe_retry_real_feed()
    assert w.feed is real_feed


@pytest.mark.asyncio
async def test_no_retry_attempted_once_on_the_real_feed(monkeypatch):
    w = Watcher(make_settings())
    real_feed = FakeFeed("real")
    w.feed = real_feed

    called = False

    async def fake_create_feed(settings):
        nonlocal called
        called = True
        return FakeFeed("unused")

    monkeypatch.setattr(watcher_module, "create_feed", fake_create_feed)

    await w._maybe_retry_real_feed()
    assert called is False
    assert w.feed is real_feed


@pytest.mark.asyncio
async def test_reconnect_keeps_old_feed_if_rebuild_fails(monkeypatch):
    w = Watcher(make_settings())
    old_feed = FakeFeed("old")
    w.feed = old_feed

    async def broken_create_feed(settings):
        raise RuntimeError("even the synthetic feed blew up")

    monkeypatch.setattr(watcher_module, "create_feed", broken_create_feed)

    await w._reconnect_feed()
    assert w.feed is old_feed
    assert old_feed.closed is False
