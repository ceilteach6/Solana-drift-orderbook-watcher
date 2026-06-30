"""
tests/test_collector.py

Tests venue selection in the feed factory. Network-free: the synthetic venue is
forced directly, and the drift venue falls back to synthetic when driftpy is not
installed.
"""

import asyncio
from types import SimpleNamespace

from src.collector.orderbook_feed import (
    SyntheticOrderbookFeed,
    create_feed,
)


def make_settings(venue):
    return SimpleNamespace(
        venue=venue,
        markets=["SOL-PERP"],
        orderbook_depth=10,
        rpc_url="http://localhost",
        drift_env="mainnet",
        keypair_path="",
        update_frequency_ms=1000,
        # read by the synthetic feed when injecting bot-like walls
        layering_min_levels=5,
        repeated_min_count=4,
    )


def test_synthetic_venue_selected():
    feed = asyncio.run(create_feed(make_settings("synthetic")))
    assert isinstance(feed, SyntheticOrderbookFeed)


def test_unknown_venue_falls_back_to_synthetic():
    feed = asyncio.run(create_feed(make_settings("does-not-exist")))
    assert isinstance(feed, SyntheticOrderbookFeed)


def test_drift_venue_falls_back_without_driftpy():
    # driftpy is not installed in the test env -> graceful synthetic fallback.
    feed = asyncio.run(create_feed(make_settings("drift")))
    assert isinstance(feed, SyntheticOrderbookFeed)


def test_synthetic_feed_produces_snapshot():
    feed = SyntheticOrderbookFeed(make_settings("synthetic"))
    asyncio.run(feed.connect())
    snap = asyncio.run(feed.get_snapshot("SOL-PERP"))
    assert snap is not None
    assert snap.market == "SOL-PERP"
    assert snap.bids and snap.asks
