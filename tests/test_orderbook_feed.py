"""
tests/test_orderbook_feed.py

Regression tests for `_snapshot_from_driftpy`: driftpy's L2 levels carry raw
fixed-point integers (unscaled) and are not guaranteed to arrive sorted by
price, but every consumer (mid price, detectors, storage) assumes
`bids[0]`/`asks[0]` is the best price on a properly-scaled book.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import (
    BASE_PRECISION,
    PRICE_PRECISION,
    _snapshot_from_driftpy,
)


class _RawLevel:
    def __init__(self, price, size):
        self.price = price
        self.size = size


def raw(price: float, size: float) -> _RawLevel:
    """Build a raw driftpy-style level from human-readable price/size."""
    return _RawLevel(int(round(price * PRICE_PRECISION)), int(round(size * BASE_PRECISION)))


def test_descales_raw_fixed_point_integers():
    l2 = SimpleNamespace(bids=[raw(150.0, 5.0)], asks=[raw(150.1, 4.0)])
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.bids[0].price == 150.0
    assert snap.bids[0].size == 5.0
    assert snap.asks[0].price == 150.1
    assert snap.asks[0].size == 4.0


def test_sorts_bids_descending_and_asks_ascending_even_if_unsorted_upstream():
    # Deliberately out of order, as an upstream DLOB merge might deliver them.
    l2 = SimpleNamespace(
        bids=[raw(149.5, 10.0), raw(150.0, 5.0), raw(149.0, 20.0)],
        asks=[raw(151.0, 8.0), raw(150.2, 3.0), raw(150.6, 1.0)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert [lvl.price for lvl in snap.bids] == [150.0, 149.5, 149.0]
    assert [lvl.price for lvl in snap.asks] == [150.2, 150.6, 151.0]


def test_mid_uses_best_bid_and_best_ask_after_sorting():
    l2 = SimpleNamespace(
        bids=[raw(149.0, 1.0), raw(150.0, 1.0)],
        asks=[raw(151.0, 1.0), raw(150.4, 1.0)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.mid == (150.0 + 150.4) / 2
