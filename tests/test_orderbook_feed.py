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


def test_snapshot_from_driftpy_sorts_levels_regardless_of_input_order():
    # Every downstream consumer (mid, best_bid/ask, spread, the imbalance
    # detector's top-N slice) assumes bids are descending and asks ascending.
    # Don't trust driftpy's DLOB to hand levels back in that order already —
    # sort defensively so an out-of-order response can't silently corrupt
    # every price-derived signal.
    l2 = SimpleNamespace(
        bids=[_lvl(100 * PRICE_PRECISION, 1 * BASE_PRECISION),
              _lvl(102 * PRICE_PRECISION, 1 * BASE_PRECISION),
              _lvl(101 * PRICE_PRECISION, 1 * BASE_PRECISION)],
        asks=[_lvl(106 * PRICE_PRECISION, 1 * BASE_PRECISION),
              _lvl(104 * PRICE_PRECISION, 1 * BASE_PRECISION),
              _lvl(105 * PRICE_PRECISION, 1 * BASE_PRECISION)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert [lvl.price for lvl in snap.bids] == [102.0, 101.0, 100.0]
    assert [lvl.price for lvl in snap.asks] == [104.0, 105.0, 106.0]


def test_snapshot_from_driftpy_handles_dict_levels():
    l2 = SimpleNamespace(
        bids=[{"price": 100 * PRICE_PRECISION, "size": 5 * BASE_PRECISION}],
        asks=[],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.bids[0].price == 100.0
    assert snap.bids[0].size == 5.0


def test_snapshot_from_driftpy_drops_malformed_levels_instead_of_zeroing_them():
    # A level missing price/size used to fall through to _to_raw()'s
    # fallback and become a fake Level(0.0, 0.0). A zero-price ask then sorts
    # to the *front* of the (ascending) ask side, becoming the new "best
    # ask" and corrupting mid price, spread, and every detector/storage row
    # derived from it. It must be dropped instead.
    l2 = SimpleNamespace(
        bids=[_lvl(100 * PRICE_PRECISION, 1 * BASE_PRECISION)],
        asks=[SimpleNamespace(price=None, size=None),
              _lvl(101 * PRICE_PRECISION, 1 * BASE_PRECISION)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert len(snap.asks) == 1
    assert snap.asks[0].price == 101.0
    assert snap.mid == 100.5


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
