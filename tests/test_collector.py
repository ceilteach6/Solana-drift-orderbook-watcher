"""
tests/test_collector.py

Tests the normalization boundary between raw driftpy L2 objects and the
internal :class:`OrderbookSnapshot` model.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import _snapshot_from_driftpy


def test_snapshot_from_driftpy_sorts_bids_desc_and_asks_asc():
    # driftpy does not guarantee level order; feed them in scrambled order.
    l2 = SimpleNamespace(
        bids=[
            SimpleNamespace(price=99.7, size=1.0),
            SimpleNamespace(price=100.0, size=2.0),
            SimpleNamespace(price=99.9, size=3.0),
        ],
        asks=[
            SimpleNamespace(price=100.5, size=1.0),
            SimpleNamespace(price=100.1, size=2.0),
            SimpleNamespace(price=100.3, size=3.0),
        ],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert [b.price for b in snap.bids] == [100.0, 99.9, 99.7]  # descending
    assert [a.price for a in snap.asks] == [100.1, 100.3, 100.5]  # ascending
    # mid relies on bids[0]/asks[0] being the best price.
    assert snap.mid == (100.0 + 100.1) / 2


def test_snapshot_from_driftpy_handles_dict_levels():
    l2 = SimpleNamespace(
        bids=[{"price": 50.0, "size": 1.0}, {"price": 51.0, "size": 1.0}],
        asks=[{"price": 53.0, "size": 1.0}, {"price": 52.0, "size": 1.0}],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert [b.price for b in snap.bids] == [51.0, 50.0]
    assert [a.price for a in snap.asks] == [52.0, 53.0]
