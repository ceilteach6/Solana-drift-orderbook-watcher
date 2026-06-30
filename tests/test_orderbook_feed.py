"""
tests/test_orderbook_feed.py

Regression test for ``_snapshot_from_driftpy``: every detector and the
mid/spread calculation assume ``bids`` is sorted descending and ``asks``
ascending (best price first). driftpy's L2 helper does not guarantee this
ordering, so the conversion must sort defensively. Network-free.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import _snapshot_from_driftpy


def _level(price, size):
    return SimpleNamespace(price=price, size=size)


def test_snapshot_from_driftpy_sorts_unordered_levels():
    # Deliberately out of order and not best-price-first, as a differently
    # versioned driftpy might return.
    l2 = SimpleNamespace(
        bids=[_level(99.5, 1.0), _level(100.0, 2.0), _level(99.8, 3.0)],
        asks=[_level(100.6, 1.0), _level(100.2, 2.0), _level(100.4, 3.0)],
    )
    snapshot = _snapshot_from_driftpy("SOL-PERP", l2)

    assert [lvl.price for lvl in snapshot.bids] == [100.0, 99.8, 99.5]
    assert [lvl.price for lvl in snapshot.asks] == [100.2, 100.4, 100.6]
    # Best bid/ask must be first for mid/spread to be correct.
    assert snapshot.mid == (100.0 + 100.2) / 2
