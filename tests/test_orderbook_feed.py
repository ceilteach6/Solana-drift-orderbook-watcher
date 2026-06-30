"""
tests/test_orderbook_feed.py

Regression test: ``_snapshot_from_driftpy`` must sort bids descending / asks
ascending regardless of the order driftpy returns levels in, since
``OrderbookSnapshot.mid`` and every detector assume ``bids[0]``/``asks[0]``
are the best prices.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import _snapshot_from_driftpy


def _level(price, size):
    return SimpleNamespace(price=price, size=size)


def test_unsorted_driftpy_levels_are_normalized():
    l2 = SimpleNamespace(
        bids=[_level(99.0, 1.0), _level(100.0, 2.0), _level(99.5, 1.5)],
        asks=[_level(101.0, 1.0), _level(100.5, 2.0), _level(100.8, 1.5)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)

    assert [lvl.price for lvl in snap.bids] == [100.0, 99.5, 99.0]
    assert [lvl.price for lvl in snap.asks] == [100.5, 100.8, 101.0]
    assert snap.mid == (100.0 + 100.5) / 2
