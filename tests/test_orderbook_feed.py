"""
tests/test_orderbook_feed.py

Unit tests for the driftpy L2 -> OrderbookSnapshot normalization. A malformed
level (unparseable price/size) must be dropped, not silently coerced to 0.0 —
a fabricated 0.0 price/size would corrupt the mid-price and feed garbage into
every detector.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import _snapshot_from_driftpy, _to_float


def test_to_float_passes_through_numeric():
    assert _to_float(1.5) == 1.5
    assert _to_float("2.5") == 2.5


def test_to_float_unwraps_value_attribute():
    assert _to_float(SimpleNamespace(value=3)) == 3.0


def test_to_float_returns_none_for_unparseable():
    assert _to_float(object()) is None
    assert _to_float(None) is None


def test_snapshot_skips_malformed_levels():
    good_bid = SimpleNamespace(price=100.0, size=5.0)
    bad_bid = SimpleNamespace(price=object(), size=5.0)  # unparseable price
    good_ask = SimpleNamespace(price=101.0, size=3.0)

    l2 = SimpleNamespace(bids=[good_bid, bad_bid], asks=[good_ask])
    snapshot = _snapshot_from_driftpy("SOL-PERP", l2)

    assert len(snapshot.bids) == 1
    assert snapshot.bids[0].price == 100.0
    assert len(snapshot.asks) == 1
    assert snapshot.mid == 100.5


def test_snapshot_handles_dict_levels():
    l2 = SimpleNamespace(
        bids=[{"price": 99.0, "size": 1.0}],
        asks=[{"price": 100.0, "size": 1.0}],
    )
    snapshot = _snapshot_from_driftpy("SOL-PERP", l2)
    assert snapshot.bids[0].price == 99.0
    assert snapshot.asks[0].price == 100.0
