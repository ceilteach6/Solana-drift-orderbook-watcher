"""
tests/test_collector.py

Tests the driftpy -> normalized snapshot conversion: malformed levels are
dropped (not silently turned into fake zero-price levels), and bids/asks are
sorted into the order every downstream consumer assumes (descending /
ascending by price), regardless of what order driftpy returns them in.
"""

from types import SimpleNamespace

from src.collector.orderbook_feed import _snapshot_from_driftpy, _to_float


def lvl(price, size):
    return SimpleNamespace(price=price, size=size)


def test_to_float_accepts_plain_numbers():
    assert _to_float(1) == 1.0
    assert _to_float("2.5") == 2.5


def test_to_float_unwraps_value_attribute():
    assert _to_float(SimpleNamespace(value=42)) == 42.0


def test_to_float_raises_on_unparsable():
    try:
        _to_float(object())
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_snapshot_sorts_bids_descending_and_asks_ascending():
    l2 = SimpleNamespace(
        bids=[lvl(99.0, 1.0), lvl(101.0, 2.0), lvl(100.0, 3.0)],
        asks=[lvl(103.0, 1.0), lvl(101.5, 2.0), lvl(102.0, 3.0)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    assert [b.price for b in snap.bids] == [101.0, 100.0, 99.0]
    assert [a.price for a in snap.asks] == [101.5, 102.0, 103.0]
    assert snap.mid == (101.0 + 101.5) / 2


def test_snapshot_drops_unparsable_levels_instead_of_zeroing_them():
    l2 = SimpleNamespace(
        bids=[lvl(100.0, 1.0), lvl(object(), 2.0)],
        asks=[lvl(101.0, 1.0)],
    )
    snap = _snapshot_from_driftpy("SOL-PERP", l2)
    # The malformed level must be dropped, not turned into Level(0.0, 2.0)
    # which would sort to the front and corrupt best-bid/mid.
    assert len(snap.bids) == 1
    assert snap.bids[0].price == 100.0
