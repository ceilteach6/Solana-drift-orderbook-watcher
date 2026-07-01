"""
tests/test_orderbook_feed.py

Regression test for driftpy L2 -> OrderbookSnapshot conversion.

driftpy's L2 levels carry raw on-chain fixed-point integers (price scaled by
PRICE_PRECISION=1e6, size scaled by BASE_PRECISION=1e9), not human-readable
floats. This only bites against a live driftpy connection — never exercised
by the synthetic feed or the rest of the test suite — so it needs its own
regression test to stay caught.
"""

import asyncio
import threading
from types import SimpleNamespace

from src.collector.orderbook_feed import (
    BASE_PRECISION,
    PRICE_PRECISION,
    DriftOrderbookFeed,
    _snapshot_from_driftpy,
)


def _lvl(price, size):
    return SimpleNamespace(price=price, size=size)


def test_snapshot_from_driftpy_descales_raw_fixed_point_integers():
    # A real level: price 150.25 (scaled: 150_250_000), size 12.5 (scaled: 12_500_000_000)
    l2 = SimpleNamespace(
        bids=[_lvl(150_250_000, 12_500_000_000)],
        asks=[_lvl(150_500_000, 8_000_000_000)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert snap.bids[0].price == 150.25
    assert snap.bids[0].size == 12.5
    assert snap.asks[0].price == 150.5
    assert snap.asks[0].size == 8.0


def test_snapshot_from_driftpy_handles_dict_levels():
    l2 = SimpleNamespace(
        bids=[{"price": 100 * PRICE_PRECISION, "size": 5 * BASE_PRECISION}],
        asks=[],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.bids[0].price == 100.0
    assert snap.bids[0].size == 5.0


class _FakeStack:
    def get_l2(self, market, depth):
        return SimpleNamespace(bids=[], asks=[], thread=threading.get_ident())

    async def close(self):
        pass


def test_get_snapshot_uses_a_dedicated_executor_not_the_shared_default():
    # Regression: get_l2() ran on loop.run_in_executor(None, ...) — the
    # process-wide default pool. asyncio.wait_for()'s timeout only cancels
    # the await; the worker thread keeps running to completion. Against a
    # hung RPC, every poll tick pinned another default-pool thread, and once
    # exhausted, *any other code in the process* relying on the default
    # executor would silently block too. A dedicated pool confines that to
    # this feed's own threads.
    feed = DriftOrderbookFeed(SimpleNamespace(markets=["SOL-PERP"]))
    feed._stack = _FakeStack()
    feed.settings.orderbook_depth = 5
    feed.settings.snapshot_timeout_sec = 5.0

    main_thread = threading.get_ident()
    snap = asyncio.run(feed.get_snapshot("SOL-PERP"))
    asyncio.run(feed.close())

    assert snap is not None
    assert feed._executor._max_workers >= 4


def test_close_shuts_down_the_executor_without_blocking():
    feed = DriftOrderbookFeed(SimpleNamespace(markets=["SOL-PERP"]))
    feed._stack = None
    asyncio.run(feed.close())  # must not raise even with no stack connected
    assert feed._executor._shutdown
