"""
tests/test_orderbook_feed.py

Regression tests for the driftpy L2 -> OrderbookSnapshot conversion.
Network-free (uses plain objects/dicts standing in for driftpy L2 levels).
"""

import asyncio
import time
from types import SimpleNamespace

import pytest

from config.settings import Settings
from src.collector.orderbook_feed import (
    BASE_PRECISION,
    PRICE_PRECISION,
    DriftOrderbookFeed,
    StaleFeedError,
    _snapshot_from_driftpy,
)


def raw_level(price, size):
    return SimpleNamespace(price=price, size=size)


def test_snapshot_from_driftpy_descales_fixed_point_price_and_size():
    # A $150.25 bid for 10.5 tokens, as driftpy actually represents it: raw
    # integers scaled by PRICE_PRECISION (1e6) / BASE_PRECISION (1e9).
    l2 = SimpleNamespace(
        bids=[raw_level(150_250_000, 10_500_000_000)],
        asks=[raw_level(150_500_000, 2_000_000_000)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.bids[0].price == 150.25
    assert snap.bids[0].size == 10.5
    assert snap.asks[0].price == 150.5
    assert snap.asks[0].size == 2.0


def test_snapshot_from_driftpy_sorts_best_price_first():
    # Deliberately out of order, as driftpy's L2 helper gives no ordering
    # guarantee; bids[0]/asks[0] must still be the best price after scaling.
    l2 = SimpleNamespace(
        bids=[
            raw_level(149 * PRICE_PRECISION, 1 * BASE_PRECISION),
            raw_level(150 * PRICE_PRECISION, 1 * BASE_PRECISION),
        ],
        asks=[
            raw_level(152 * PRICE_PRECISION, 1 * BASE_PRECISION),
            raw_level(151 * PRICE_PRECISION, 1 * BASE_PRECISION),
        ],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.bids[0].price == 150.0
    assert snap.asks[0].price == 151.0
    assert snap.mid == 150.5


# --------------------------------------------------------------------------- #
# Stale-DLOB detection
# --------------------------------------------------------------------------- #
class _FakeStack:
    """Stands in for DriftStack.get_l2 — returns whatever raw L2 is set."""

    def __init__(self, l2):
        self.l2 = l2

    def get_l2(self, market, depth=20):
        return self.l2


def make_settings(**overrides):
    base = dict(rpc_url="http://localhost", drift_env="devnet", keypair_path="",
                markets=("SOL-PERP",))
    base.update(overrides)
    return Settings(**base)


def _l2(price=150.0):
    return SimpleNamespace(
        bids=[raw_level(price * PRICE_PRECISION, 1 * BASE_PRECISION)],
        asks=[raw_level((price + 1) * PRICE_PRECISION, 1 * BASE_PRECISION)],
    )


def test_unchanged_book_raises_stale_feed_error_after_threshold():
    feed = DriftOrderbookFeed(make_settings(dlob_stale_after_sec=0.05))
    feed._stack = _FakeStack(_l2())

    async def run():
        await feed.get_snapshot("SOL-PERP")  # establishes the baseline
        await asyncio.sleep(0.08)
        with pytest.raises(StaleFeedError):
            await feed.get_snapshot("SOL-PERP")

    asyncio.run(run())


def test_changing_book_never_raises_stale_feed_error():
    feed = DriftOrderbookFeed(make_settings(dlob_stale_after_sec=0.05))
    stack = _FakeStack(_l2(150.0))
    feed._stack = stack

    async def run():
        for i in range(5):
            stack.l2 = _l2(150.0 + i)  # book changes every poll
            await feed.get_snapshot("SOL-PERP")
            await asyncio.sleep(0.02)

    asyncio.run(run())  # must not raise


def test_zero_disables_stale_feed_check():
    feed = DriftOrderbookFeed(make_settings(dlob_stale_after_sec=0))
    feed._stack = _FakeStack(_l2())

    async def run():
        await feed.get_snapshot("SOL-PERP")
        await asyncio.sleep(0.05)
        await feed.get_snapshot("SOL-PERP")  # must not raise, check is off

    asyncio.run(run())
