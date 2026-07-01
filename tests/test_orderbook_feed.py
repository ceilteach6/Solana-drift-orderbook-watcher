"""
tests/test_orderbook_feed.py

Regression tests for the driftpy L2 -> OrderbookSnapshot conversion.
Network-free (uses plain objects/dicts standing in for driftpy L2 levels).
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import (
    BASE_PRECISION,
    PRICE_PRECISION,
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
