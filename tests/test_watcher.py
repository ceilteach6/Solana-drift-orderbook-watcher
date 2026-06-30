"""
tests/test_watcher.py

Watcher-level resilience tests. These guard the production failure paths the
unit/algorithmic tests don't reach: a buggy aggregator must not kill the
process, a dead feed must trigger a reconnect, and a broken store must stop
itself rather than fail (and log) forever.
"""

import pytest

import src.watcher as watcher_mod
from config.settings import Settings
from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=["SOL-PERP"],
        healthcheck_enabled=False,
        storage_enabled=False,
        feed_reconnect_after_failures=2,
        feed_reconnect_cooldown_sec=0.0,
        storage_failure_circuit_breaker=2,
    )
    base.update(overrides)
    return Settings(**base)


def snap(market="SOL-PERP", ts=1.0):
    return OrderbookSnapshot(
        market=market, timestamp=ts,
        bids=[Level(100.0, 5.0)], asks=[Level(100.1, 4.0)],
    )


class FakeFeed:
    def __init__(self):
        self.closed = False

    async def get_snapshot(self, market):
        return snap()

    async def close(self):
        self.closed = True


class RaisingFeed(FakeFeed):
    async def get_snapshot(self, market):
        raise RuntimeError("websocket dropped")


class RaisingAggregator:
    def update(self, market, timestamp, detections):
        raise RuntimeError("boom")

    def score(self, market):
        return 0.0


@pytest.mark.asyncio
async def test_tick_survives_aggregator_exception():
    w = Watcher(make_settings())
    w.feed = FakeFeed()
    w.aggregator = RaisingAggregator()
    await w._tick("SOL-PERP")  # must not raise


@pytest.mark.asyncio
async def test_feed_reconnects_after_consecutive_failures(monkeypatch):
    settings = make_settings(feed_reconnect_after_failures=2)
    w = Watcher(settings)
    dead_feed = RaisingFeed()
    w.feed = dead_feed

    new_feed = FakeFeed()
    calls = []

    async def fake_create_feed(s):
        calls.append(s)
        return new_feed

    monkeypatch.setattr(watcher_mod, "create_feed", fake_create_feed)

    await w._tick("SOL-PERP")  # failure 1, below threshold
    assert w.feed is dead_feed
    assert calls == []

    await w._tick("SOL-PERP")  # failure 2, hits threshold -> reconnect
    assert calls == [settings]
    assert w.feed is new_feed
    assert dead_feed.closed is True
    assert w._feed_failure_streak == 0


class FailingStore:
    def __init__(self):
        self.closed = False

    def record_snapshot(self, snapshot):
        pass

    def record_detections(self, ts, detections):
        raise RuntimeError("disk full")

    def record_risk(self, market, ts, score, mid=None):
        pass

    def close(self):
        self.closed = True


def test_persist_disables_storage_after_circuit_breaker_trips():
    settings = make_settings(
        storage_enabled=False, risk_aggregation=False,
        storage_failure_circuit_breaker=2,
    )
    w = Watcher(settings)
    store = FailingStore()
    w.store = store

    w._persist("SOL-PERP", snap(), [])
    assert w.store is store  # 1st failure: below breaker threshold

    w._persist("SOL-PERP", snap(), [])
    assert w.store is None  # 2nd failure: breaker trips
    assert store.closed is True
