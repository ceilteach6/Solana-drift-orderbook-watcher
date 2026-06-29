"""
tests/test_session_baseline.py

Unit tests for the SessionBaselineDetector.
Network-free — uses hand-built mock orderbooks.

Run:
    pytest tests/test_session_baseline.py -v
"""

import time
from types import SimpleNamespace

from src.collector.orderbook_feed import Level, OrderbookSnapshot
from src.detector.session_baseline import SessionBaselineDetector


def make_settings(**overrides):
    base = dict(
        baseline_warmup_cycles=5,
        baseline_spike_ratio=3.0,
        baseline_drain_ratio=0.2,
        baseline_ema_alpha=0.5,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def snap(total_size: float, market: str = "SOL-PERP") -> OrderbookSnapshot:
    """Build a snapshot where all volume sits on one bid level."""
    return OrderbookSnapshot(
        market=market,
        timestamp=time.time(),
        bids=[Level(100.0, total_size)],
        asks=[Level(101.0, 0.0)],
    )


def _warm_up(det, market: str = "SOL-PERP", vol: float = 100.0, cycles: int = 6):
    """Feed the detector enough stable snapshots to leave warm-up."""
    for _ in range(cycles):
        det.analyze(snap(vol, market), [])


# --------------------------------------------------------------------------- #
# Warm-up behaviour
# --------------------------------------------------------------------------- #
def test_no_detection_during_warmup():
    det = SessionBaselineDetector(make_settings(baseline_warmup_cycles=10))
    # Even a massive volume shouldn't fire during warm-up.
    for _ in range(9):
        result = det.analyze(snap(1_000_000.0), [])
        assert result == [], "Should not fire before warmup is complete"


def test_fires_after_warmup():
    det = SessionBaselineDetector(make_settings())
    _warm_up(det)
    # Now inject a spike 10× above the stable baseline (~100).
    result = det.analyze(snap(10_000.0), [])
    assert len(result) == 1
    assert result[0].details["kind"] == "spike"


# --------------------------------------------------------------------------- #
# Spike detection
# --------------------------------------------------------------------------- #
def test_spike_score_is_bounded():
    det = SessionBaselineDetector(make_settings())
    _warm_up(det)
    result = det.analyze(snap(999_999.0), [])
    assert result
    assert 0.0 < result[0].score <= 1.0


def test_no_spike_on_stable_volume():
    det = SessionBaselineDetector(make_settings())
    _warm_up(det)
    # Volume close to the baseline (100 ± 20%) should not spike.
    result = det.analyze(snap(110.0), [])
    assert result == []


def test_spike_message_and_details():
    det = SessionBaselineDetector(make_settings())
    _warm_up(det)
    result = det.analyze(snap(5_000.0), [])
    assert result
    d = result[0]
    assert d.detector == "volume_spike"
    assert "spike" in d.message.lower()
    assert d.details["kind"] == "spike"
    assert d.details["ratio"] > det.settings.baseline_spike_ratio


# --------------------------------------------------------------------------- #
# Drain detection
# --------------------------------------------------------------------------- #
def test_drain_detected_after_warmup():
    det = SessionBaselineDetector(make_settings(baseline_drain_ratio=0.3))
    _warm_up(det, vol=1000.0)
    # Inject near-zero volume — way below 30% of 1000.
    result = det.analyze(snap(1.0), [])
    assert result, "Drain should be detected"
    assert result[0].details["kind"] == "drain"


def test_no_drain_on_moderate_drop():
    det = SessionBaselineDetector(make_settings(baseline_drain_ratio=0.2))
    _warm_up(det, vol=100.0)
    # 50% of baseline — above the 20% drain threshold.
    result = det.analyze(snap(50.0), [])
    assert result == []


# --------------------------------------------------------------------------- #
# Multi-market isolation
# --------------------------------------------------------------------------- #
def test_independent_ema_per_market():
    det = SessionBaselineDetector(make_settings())
    # Warm up SOL-PERP with stable 100 vol.
    _warm_up(det, market="SOL-PERP", vol=100.0)
    # Warm up BTC-PERP with stable 200 vol.
    _warm_up(det, market="BTC-PERP", vol=200.0)

    # Spike on SOL-PERP should not affect BTC-PERP.
    r_sol = det.analyze(snap(10_000.0, "SOL-PERP"), [])
    r_btc = det.analyze(snap(210.0, "BTC-PERP"), [])

    assert len(r_sol) == 1, "SOL-PERP should fire (spike)"
    assert r_btc == [], "BTC-PERP should be quiet (normal volume)"
