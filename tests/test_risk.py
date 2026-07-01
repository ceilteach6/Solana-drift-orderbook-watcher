"""
tests/test_risk.py

Unit tests for the risk aggregator: score combination, EMA smoothing, and the
hysteresis + cooldown emission gate. Network-free.
"""

from types import SimpleNamespace

from src.detector.base import Detection
from src.risk.aggregator import RiskAggregator


def make_settings(**overrides):
    base = dict(
        risk_smoothing=1.0,  # no smoothing by default -> instant = smoothed
        risk_alert_threshold=0.6,
        risk_clear_threshold=0.4,
        risk_alert_cooldown_sec=30.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def det(detector, score, market="SOL-PERP"):
    return Detection(detector=detector, market=market, score=score, message="x")


def test_noisy_or_combines_multiple_detectors():
    agg = RiskAggregator(make_settings())
    # 0.5 and 0.5 -> 1 - 0.5*0.5 = 0.75
    out = agg.update("SOL-PERP", 0.0, [det("a", 0.5), det("b", 0.5)])
    assert out is not None
    assert round(out.details["risk"], 2) == 0.75


def test_strongest_per_detector_wins():
    agg = RiskAggregator(make_settings(risk_alert_threshold=0.95))
    # Same detector twice: only the strongest (0.8) counts, not noisy-OR of both.
    out = agg.update("SOL-PERP", 0.0, [det("a", 0.3), det("a", 0.8)])
    # 0.8 < 0.95 threshold -> no alert, but score recorded
    assert out is None
    assert round(agg.score("SOL-PERP"), 2) == 0.8


def test_no_alert_below_threshold():
    agg = RiskAggregator(make_settings())
    assert agg.update("SOL-PERP", 0.0, [det("a", 0.4)]) is None


def test_cooldown_suppresses_repeat_then_allows():
    agg = RiskAggregator(make_settings(risk_alert_cooldown_sec=30))
    strong = [det("a", 0.9)]
    assert agg.update("SOL-PERP", 0.0, strong) is not None      # opening alert
    assert agg.update("SOL-PERP", 10.0, strong) is None         # within cooldown
    assert agg.update("SOL-PERP", 40.0, strong) is not None     # cooldown elapsed


def test_hysteresis_clears_then_realerts():
    agg = RiskAggregator(make_settings())
    assert agg.update("SOL-PERP", 0.0, [det("a", 0.9)]) is not None  # elevated
    # Drop below clear threshold -> state clears, no alert
    assert agg.update("SOL-PERP", 1.0, [det("a", 0.2)]) is None
    assert agg.score("SOL-PERP") < 0.4
    # New spike re-opens an alert (not gated by the previous cooldown)
    assert agg.update("SOL-PERP", 2.0, [det("a", 0.9)]) is not None


def test_ema_smoothing_requires_sustained_signal():
    agg = RiskAggregator(make_settings(risk_smoothing=0.4, risk_alert_threshold=0.6))
    strong = [det("a", 0.9)]
    # One tick: 0.4*0.9 = 0.36 -> below threshold, no alert yet.
    assert agg.update("SOL-PERP", 0.0, strong) is None
    # Sustained ticks push the EMA over the threshold.
    fired = None
    for t in range(1, 6):
        fired = agg.update("SOL-PERP", float(t), strong) or fired
    assert fired is not None


def test_nan_score_is_dropped_instead_of_poisoning_the_ema():
    agg = RiskAggregator(make_settings())
    # A NaN score (e.g. from a buggy detector) must not enter the EMA — once
    # smoothed = NaN it would stay NaN on every subsequent tick forever.
    out = agg.update("SOL-PERP", 0.0, [det("a", float("nan"))])
    assert out is None
    assert agg.score("SOL-PERP") == 0.0

    # And the aggregator keeps working normally afterwards.
    out = agg.update("SOL-PERP", 1.0, [det("a", 0.9)])
    assert out is not None
    assert agg.score("SOL-PERP") == 0.9
