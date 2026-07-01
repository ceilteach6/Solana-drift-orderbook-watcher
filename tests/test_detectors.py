"""
tests/test_detectors.py

Unit tests for the detector stack using hand-built mock orderbooks.
Network-free and driftpy-free.

Run:
    pytest
"""

import time
from types import SimpleNamespace

import pytest

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector.flicker import FlickerDetector
from src.detector.imbalance import ImbalanceDetector
from src.detector.layering import LayeringDetector
from src.detector.repeated_size import RepeatedSizeDetector
from src.detector.spoof_pull import SpoofPullDetector


def make_settings(**overrides):
    base = dict(
        repeated_min_count=4,
        repeated_size_tolerance=0.001,
        layering_min_levels=5,
        flicker_window_sec=5.0,
        flicker_min_events=3,
        imbalance_min_ratio=0.85,
        imbalance_min_levels=5,
        spoof_window_sec=10.0,
        spoof_wall_ratio=5.0,
        spoof_min_price_move=0.001,
        spoof_pull_fraction=0.5,
        alert_min_score=0.6,
        alert_format="console",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def snap(bids=None, asks=None, market="SOL-PERP", ts=None):
    return OrderbookSnapshot(
        market=market,
        timestamp=ts if ts is not None else time.time(),
        bids=[Level(p, s) for p, s in (bids or [])],
        asks=[Level(p, s) for p, s in (asks or [])],
    )


# --------------------------------------------------------------------------- #
# Repeated size
# --------------------------------------------------------------------------- #
def test_repeated_size_fires_on_identical_sizes():
    det = RepeatedSizeDetector(make_settings())
    s = snap(
        bids=[(100, 10.0), (99, 10.0), (98, 10.0)],
        asks=[(101, 10.0), (102, 3.3)],
    )
    detections = det.analyze(s, [])
    assert len(detections) == 1
    assert detections[0].details["count"] == 4
    assert detections[0].score > 0


def test_repeated_size_quiet_on_varied_sizes():
    det = RepeatedSizeDetector(make_settings())
    s = snap(
        bids=[(100, 1.0), (99, 2.0), (98, 3.0)],
        asks=[(101, 4.0), (102, 5.0)],
    )
    assert det.analyze(s, []) == []


# --------------------------------------------------------------------------- #
# Layering
# --------------------------------------------------------------------------- #
def test_layering_fires_on_one_sided_wall():
    det = LayeringDetector(make_settings(layering_min_levels=5))
    s = snap(
        bids=[(100 - i, 50.0) for i in range(6)],  # 6-level wall
        asks=[(101, 1.0), (102, 2.0)],
    )
    detections = det.analyze(s, [])
    assert len(detections) == 1
    assert detections[0].details["side"] == "bid"
    assert detections[0].details["levels"] == 6


def test_layering_quiet_when_below_threshold():
    det = LayeringDetector(make_settings(layering_min_levels=5))
    s = snap(bids=[(100 - i, 50.0) for i in range(3)], asks=[(101, 1.0)])
    assert det.analyze(s, []) == []


# --------------------------------------------------------------------------- #
# Flicker
# --------------------------------------------------------------------------- #
def test_flicker_fires_on_toggling_level():
    det = FlickerDetector(make_settings(flicker_min_events=3, flicker_window_sec=10))
    base = 1000.0
    history = []
    # Bid levels @ 99/100 alternate present/absent — both genuinely flicker,
    # so both should be reported (not just whichever ties best internally).
    for i in range(6):
        present = i % 2 == 0
        bids = [(100.0, 5.0)] if present else [(99.0, 5.0)]
        history.append(snap(bids=bids, asks=[(101.0, 1.0)], ts=base + i))
    current = history.pop()  # newest is the "current" snapshot
    detections = det.analyze(current, history)
    assert len(detections) == 2
    prices = {d.details["price"] for d in detections}
    assert prices == {99.0, 100.0}
    for d in detections:
        assert d.details["transitions"] >= 3
        assert d.details["side"] == "bid"


def test_flicker_reports_only_the_flickering_level():
    det = FlickerDetector(make_settings(flicker_min_events=3, flicker_window_sec=10))
    base = 3000.0
    history = []
    # Bid @ 100 toggles; ask @ 101 stays put the whole time.
    for i in range(6):
        present = i % 2 == 0
        bids = [(100.0, 5.0)] if present else []
        history.append(snap(bids=bids, asks=[(101.0, 1.0)], ts=base + i))
    current = history.pop()
    detections = det.analyze(current, history)
    assert len(detections) == 1
    assert detections[0].details["side"] == "bid"
    assert detections[0].details["price"] == 100.0


def test_flicker_quiet_on_stable_book():
    det = FlickerDetector(make_settings(flicker_min_events=3, flicker_window_sec=10))
    base = 2000.0
    history = [
        snap(bids=[(100.0, 5.0)], asks=[(101.0, 1.0)], ts=base + i) for i in range(6)
    ]
    current = history.pop()
    assert det.analyze(current, history) == []


def test_flicker_needs_minimum_samples():
    det = FlickerDetector(make_settings())
    s = snap(bids=[(100.0, 5.0)], asks=[(101.0, 1.0)])
    assert det.analyze(s, []) == []


# --------------------------------------------------------------------------- #
# Imbalance
# --------------------------------------------------------------------------- #
def test_imbalance_fires_on_one_sided_book():
    det = ImbalanceDetector(make_settings(imbalance_min_ratio=0.85, imbalance_min_levels=5))
    s = snap(
        bids=[(100 - i, 100.0) for i in range(5)],  # heavy bid side
        asks=[(101 + i, 1.0) for i in range(5)],
    )
    detections = det.analyze(s, [])
    assert len(detections) == 1
    assert detections[0].details["side"] == "bid"
    assert detections[0].details["imbalance"] > 0.85


def test_imbalance_quiet_on_balanced_book():
    det = ImbalanceDetector(make_settings(imbalance_min_ratio=0.85, imbalance_min_levels=5))
    s = snap(
        bids=[(100 - i, 10.0) for i in range(5)],
        asks=[(101 + i, 10.0) for i in range(5)],
    )
    assert det.analyze(s, []) == []


def test_imbalance_quiet_on_empty_book():
    det = ImbalanceDetector(make_settings())
    assert det.analyze(snap(), []) == []


# --------------------------------------------------------------------------- #
# Spoof-pull
# --------------------------------------------------------------------------- #
def _prior_with_bid_wall(ts=0.0):
    # mid = (99.9 + 100.1) / 2 = 100.0; a 50-size wall sits at 99.5
    return snap(
        bids=[(99.9, 1.0), (99.8, 1.0), (99.7, 1.0), (99.5, 50.0)],
        asks=[(100.1, 1.0), (100.2, 1.0)],
        ts=ts,
    )


def test_spoof_pull_fires_when_wall_pulled_and_price_moves():
    det = SpoofPullDetector(make_settings())
    prior = _prior_with_bid_wall(ts=0.0)
    # Wall at 99.5 is gone; mid moved up to 100.3 (+0.3%).
    current = snap(
        bids=[(100.2, 1.0), (100.1, 1.0), (100.0, 1.0)],
        asks=[(100.4, 1.0), (100.5, 1.0)],
        ts=1.0,
    )
    detections = det.analyze(current, [prior])
    assert len(detections) == 1
    assert detections[0].details["side"] == "bid"
    assert detections[0].details["wall_size"] == 50.0
    assert detections[0].score > 0


def test_spoof_pull_fires_when_wall_pulled_early_in_window():
    """Regression: the detector used to compare the price move over the
    *whole* window against a wall lookup on only the single immediately-
    preceding tick. A wall pulled early in the window — with the price move
    only showing up several ticks later — was silently missed because by
    the time "previous" was checked, the wall was already long gone from
    that snapshot too. The fix scans every prior snapshot for the wall and
    ties the price-move check to that snapshot's own timestamp."""
    det = SpoofPullDetector(make_settings())
    history = [_prior_with_bid_wall(ts=0.0)]  # wall present at t=0
    # Wall is gone from t=1 onward; book stays flat until the price finally
    # moves at t=9 ("now") — several ticks after the wall actually vanished.
    for t in range(1, 9):
        history.append(
            snap(
                bids=[(99.9, 1.0), (99.8, 1.0), (99.7, 1.0)],
                asks=[(100.1, 1.0), (100.2, 1.0)],
                ts=float(t),
            )
        )
    current = snap(
        bids=[(100.6, 1.0), (100.5, 1.0)],
        asks=[(100.8, 1.0), (100.9, 1.0)],
        ts=9.0,
    )
    detections = det.analyze(current, history)
    assert len(detections) == 1
    assert detections[0].details["side"] == "bid"
    assert detections[0].details["wall_size"] == 50.0


def test_spoof_pull_quiet_when_wall_persists():
    det = SpoofPullDetector(make_settings())
    prior = _prior_with_bid_wall(ts=0.0)
    # Price moved, but the 99.5 wall is still there -> not pulled.
    current = snap(
        bids=[(100.2, 1.0), (100.0, 1.0), (99.5, 50.0)],
        asks=[(100.4, 1.0), (100.5, 1.0)],
        ts=1.0,
    )
    assert det.analyze(current, [prior]) == []


def test_spoof_pull_quiet_without_price_move():
    det = SpoofPullDetector(make_settings())
    prior = _prior_with_bid_wall(ts=0.0)
    # Wall gone, but mid is unchanged (~100.0) -> no correlated move.
    current = snap(
        bids=[(99.9, 1.0), (99.8, 1.0)],
        asks=[(100.1, 1.0), (100.2, 1.0)],
        ts=1.0,
    )
    assert det.analyze(current, [prior]) == []


def test_spoof_pull_quiet_without_wall():
    det = SpoofPullDetector(make_settings())
    # No oversized wall in prior; price moves anyway.
    prior = snap(
        bids=[(99.9, 1.0), (99.8, 1.0), (99.7, 1.0)],
        asks=[(100.1, 1.0), (100.2, 1.0)],
        ts=0.0,
    )
    current = snap(
        bids=[(100.2, 1.0)], asks=[(100.4, 1.0)], ts=1.0
    )
    assert det.analyze(current, [prior]) == []


def test_spoof_pull_needs_history():
    det = SpoofPullDetector(make_settings())
    assert det.analyze(_prior_with_bid_wall(), []) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
