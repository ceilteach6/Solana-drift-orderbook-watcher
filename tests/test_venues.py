"""
tests/test_venues.py

Tests the multi-venue collector layer: market-spec parsing, the venue
registry, the MultiVenueFeed router, and the FEED_MODE fallback semantics
(auto / live / synthetic).
"""

import asyncio

import pytest

from config.settings import Settings
from src.collector import orderbook_feed as of
from src.collector.orderbook_feed import (
    MultiVenueFeed,
    OrderbookFeed,
    SyntheticOrderbookFeed,
    create_feed,
    parse_market,
)


def make_settings(**overrides) -> Settings:
    base = dict(
        rpc_url="http://unused",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
    )
    base.update(overrides)
    return Settings(**base)


class _BoomFeed(OrderbookFeed):
    """A venue feed whose connect() always fails (stands in for a dead RPC)."""

    def __init__(self, settings) -> None:
        pass

    async def connect(self) -> None:
        raise ConnectionError("nope")


# --------------------------------------------------------------------------- #
# Market spec parsing
# --------------------------------------------------------------------------- #
def test_parse_market_bare_name_uses_default_venue():
    assert parse_market("SOL-PERP") == ("drift", "SOL-PERP")


def test_parse_market_prefixed_and_normalized():
    assert parse_market("phoenix:SOL/USDC") == ("phoenix", "SOL/USDC")
    assert parse_market(" Phoenix : SOL/USDC ") == ("phoenix", "SOL/USDC")


def test_parse_market_rejects_malformed_specs():
    with pytest.raises(ValueError):
        parse_market("phoenix:")
    with pytest.raises(ValueError):
        parse_market(":SOL-PERP")


# --------------------------------------------------------------------------- #
# FEED_MODE setting
# --------------------------------------------------------------------------- #
def test_feed_mode_validation():
    for mode in ("auto", "live", "synthetic"):
        assert make_settings(feed_mode=mode).feed_mode == mode
    with pytest.raises(ValueError):
        make_settings(feed_mode="demo")


# --------------------------------------------------------------------------- #
# MultiVenueFeed routing
# --------------------------------------------------------------------------- #
def test_synthetic_mode_serves_every_venue_without_touching_real_feeds(monkeypatch):
    # phoenix's factory would blow up if invoked — synthetic mode must not.
    monkeypatch.setitem(of.VENUE_FEEDS, "phoenix", _BoomFeed)
    settings = make_settings(markets=("SOL-PERP", "phoenix:SOL-PERP"),
                             feed_mode="synthetic")

    async def run():
        feed = MultiVenueFeed(settings)
        await feed.connect()
        a = await feed.get_snapshot("SOL-PERP")
        b = await feed.get_snapshot("phoenix:SOL-PERP")
        await feed.close()
        return a, b

    a, b = asyncio.run(run())
    # Snapshots carry the configured spec, so downstream keys line up.
    assert a.market == "SOL-PERP"
    assert b.market == "phoenix:SOL-PERP"
    assert a.bids and b.bids


def test_auto_mode_falls_back_to_synthetic_when_venue_fails(monkeypatch):
    monkeypatch.setitem(of.VENUE_FEEDS, "phoenix", _BoomFeed)
    settings = make_settings(markets=("phoenix:SOL-PERP",), feed_mode="auto")

    async def run():
        feed = MultiVenueFeed(settings)
        await feed.connect()
        snap = await feed.get_snapshot("phoenix:SOL-PERP")
        await feed.close()
        return feed, snap

    feed, snap = asyncio.run(run())
    assert isinstance(feed._feeds["phoenix"], SyntheticOrderbookFeed)
    assert snap.market == "phoenix:SOL-PERP"


def test_live_mode_refuses_synthetic_fallback(monkeypatch):
    monkeypatch.setitem(of.VENUE_FEEDS, "phoenix", _BoomFeed)
    settings = make_settings(markets=("phoenix:SOL-PERP",), feed_mode="live")

    with pytest.raises(RuntimeError, match="FEED_MODE=live"):
        asyncio.run(MultiVenueFeed(settings).connect())


def test_unknown_venue_is_an_error_in_every_mode():
    for mode in ("auto", "live", "synthetic"):
        settings = make_settings(markets=("nosuchvenue:X",), feed_mode=mode)
        with pytest.raises(ValueError, match="nosuchvenue"):
            asyncio.run(MultiVenueFeed(settings).connect())


def test_one_feed_instance_per_venue(monkeypatch):
    # Two markets on the same (failing -> synthetic) venue share one feed.
    monkeypatch.setitem(of.VENUE_FEEDS, "phoenix", _BoomFeed)
    settings = make_settings(
        markets=("phoenix:A", "phoenix:B", "SOL-PERP"), feed_mode="auto"
    )

    async def run():
        feed = MultiVenueFeed(settings)
        await feed.connect()
        await feed.close()
        return feed

    feed = asyncio.run(run())
    # drift also falls back here (driftpy not installed in the test env), so
    # every venue routes to the single shared synthetic instance.
    assert feed._feeds["phoenix"] is feed._synthetic
    assert len({id(f) for f in feed._feeds.values()}) == 1


def test_create_feed_returns_connected_router():
    settings = make_settings(feed_mode="synthetic")

    async def run():
        feed = await create_feed(settings)
        snap = await feed.get_snapshot("SOL-PERP")
        await feed.close()
        return feed, snap

    feed, snap = asyncio.run(run())
    assert isinstance(feed, MultiVenueFeed)
    assert snap is not None and snap.market == "SOL-PERP"
