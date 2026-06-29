"""
tests/test_detectors.py

Unit tests for the detector stack using hand-built mock orderbooks.
Network-free and driftpy-free.

Run:
    pytest
    pytest -v
"""

import time
from types import SimpleNamespace

import pytest

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector.flicker import FlickerDetector
from src.detector.imbalance import ImbalanceDetector
from src.detector.layering import LayeringDetector
from src.detector.repeated_size import RepeatedSizeDetector


def make_settings(**overrides):
    base = dict(
        repeated_min_count=4,
        repeated_size_tolerance=0.001,
        layering_min_levels=5,
        flicker_window_sec=5.0,
        flicker_min_events=3,
        imbalance_min_ratio=0.85,
        imbalance_min_levels=5,
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
        bids=[(100 - i, 50.0) for i in range(6)],
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
    for i in range(6):
        present = i % 2 == 0
        bids = [(100.0, 5.0)] if present else [(99.0, 5.0)]
        history.append(snap(bids=bids, asks=[(101.0, 1.0)], ts=base + i))
    current = history.pop()
    detections = det.analyze(current, history)
    assert len(detections) == 1
    assert detections[0].details["transitions"] >= 3


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
        bids=[(100 - i, 100.0) for i in range(5)],
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
