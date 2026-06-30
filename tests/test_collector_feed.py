"""
tests/test_collector_feed.py

Unit tests for the venue-agnostic collector layer: the L2 normalizers and
the venue-dispatching create_feed() factory. Network-free — no real RPC,
driftpy, or phoenix-trade required (the real feeds are only imported
lazily, so their absence here is itself what's under test: every venue
must gracefully fall back to the synthetic feed).
"""

import asyncio
from types import SimpleNamespace

from src.collector.orderbook_feed import (
    SyntheticOrderbookFeed,
    _snapshot_from_phoenix,
    create_feed,
)


def make_settings(**overrides):
    base = dict(
        venue="drift",
        markets=["SOL-PERP"],
        rpc_url="https://api.mainnet-beta.solana.com",
        keypair_path="",
        orderbook_depth=10,
        update_frequency_ms=1000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# create_feed() venue dispatch
# --------------------------------------------------------------------------- #
def test_create_feed_falls_back_to_synthetic_for_unknown_venue():
    settings = make_settings(venue="nonexistent-venue")
    feed = asyncio.run(create_feed(settings))
    assert isinstance(feed, SyntheticOrderbookFeed)


def test_create_feed_falls_back_to_synthetic_when_drift_sdk_missing():
    # driftpy isn't installed in this environment, so the real feed's
    # connect() raises ImportError and create_feed() must still succeed.
    settings = make_settings(venue="drift")
    feed = asyncio.run(create_feed(settings))
    assert isinstance(feed, SyntheticOrderbookFeed)


def test_create_feed_falls_back_to_synthetic_when_phoenix_sdk_missing():
    settings = make_settings(venue="phoenix", markets=["4DoNfFBfF7UokCC2FQzriy7yHK6DY6NVdYpuekQ5pRgg"])
    feed = asyncio.run(create_feed(settings))
    assert isinstance(feed, SyntheticOrderbookFeed)


# --------------------------------------------------------------------------- #
# Phoenix L2 normalizer
# --------------------------------------------------------------------------- #
def test_snapshot_from_phoenix_attribute_style_levels():
    level = SimpleNamespace(price=101.5, size=2.25)
    raw = SimpleNamespace(bids=[SimpleNamespace(price=100.0, size=5.0)], asks=[level])

    snap = _snapshot_from_phoenix("4DoNfFBfF7UokCC2FQzriy7yHK6DY6NVdYpuekQ5pRgg", raw)

    assert snap.bids[0].price == 100.0
    assert snap.bids[0].size == 5.0
    assert snap.asks[0].price == 101.5
    assert snap.asks[0].size == 2.25


def test_snapshot_from_phoenix_dict_style_levels():
    raw = SimpleNamespace(
        bids=[{"price": 99.0, "size": 3.0}],
        asks=[{"price": 100.0, "size": 1.0}],
    )

    snap = _snapshot_from_phoenix("market", raw)

    assert snap.bids[0].price == 99.0
    assert snap.asks[0].size == 1.0


def test_snapshot_from_phoenix_empty_sides():
    raw = SimpleNamespace(bids=None, asks=None)
    snap = _snapshot_from_phoenix("market", raw)
    assert snap.bids == []
    assert snap.asks == []
