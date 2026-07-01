"""
tests/test_orderbook_feed.py

Tests for _snapshot_from_driftpy: driftpy's L2 helper returns levels as raw
fixed-point integers with no guaranteed ordering (see driftpy's L2Level /
DLOBSubscriber). Every consumer (mid price, imbalance detector, sqlite_store)
assumes descaled floats with bids[0]/asks[0] as the best price.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import (
    _BASE_PRECISION,
    _PRICE_PRECISION,
    _snapshot_from_driftpy,
)


class _RawLevel:
    def __init__(self, price: int, size: int):
        self.price = price
        self.size = size


def _l2(bids, asks):
    return SimpleNamespace(bids=bids, asks=asks)


def test_descales_fixed_point_price_and_size():
    l2 = _l2(
        bids=[_RawLevel(int(150.25 * _PRICE_PRECISION), int(2.0 * _BASE_PRECISION))],
        asks=[_RawLevel(int(150.30 * _PRICE_PRECISION), int(1.5 * _BASE_PRECISION))],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snap.bids[0].price == 150.25
    assert snap.bids[0].size == 2.0
    assert snap.asks[0].price == 150.30
    assert snap.asks[0].size == 1.5


def test_bids_sorted_descending_and_asks_ascending():
    # driftpy does not guarantee level ordering; unsorted input must still
    # produce bids[0]/asks[0] as the best (highest bid / lowest ask) price.
    def px(p):
        return int(p * _PRICE_PRECISION)

    def sz(s):
        return int(s * _BASE_PRECISION)

    l2 = _l2(
        bids=[_RawLevel(px(100.0), sz(1)), _RawLevel(px(100.2), sz(1)), _RawLevel(px(99.9), sz(1))],
        asks=[_RawLevel(px(100.5), sz(1)), _RawLevel(px(100.3), sz(1)), _RawLevel(px(100.4), sz(1))],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert [lvl.price for lvl in snap.bids] == [100.2, 100.0, 99.9]
    assert [lvl.price for lvl in snap.asks] == [100.3, 100.4, 100.5]
    assert snap.bids[0].price == 100.2  # best bid = highest price
    assert snap.asks[0].price == 100.3  # best ask = lowest price
    assert snap.mid == (100.2 + 100.3) / 2
