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
        alert_min_score=0.0,  # matches AlertDispatcher's gate; 0.0 = never filters here
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


def test_undeliverable_crossing_does_not_start_cooldown_for_a_later_real_alert():
    # Regression: a crossing that clears RISK_ALERT_THRESHOLD but not the
    # separately configured ALERT_MIN_SCORE used to still stamp the cooldown
    # clock (via _build()), so a later genuinely deliverable spike was
    # silently withheld until cooldown elapsed from an alert the operator
    # never actually received.
    agg = RiskAggregator(make_settings(risk_alert_threshold=0.6, alert_min_score=0.8))

    # t=0: crosses risk_alert_threshold (0.65 >= 0.6) but not alert_min_score
    # (0.65 < 0.8) -> nothing deliverable, must not arm the cooldown clock.
    assert agg.update("SOL-PERP", 0.0, [det("a", 0.65)]) is None

    # t=5: a real, deliverable spike (0.9 >= 0.8) must fire immediately, not
    # be gated by cooldown from the invisible t=0 crossing.
    out = agg.update("SOL-PERP", 5.0, [det("a", 0.9)])
    assert out is not None
    assert round(out.details["risk"], 2) == 0.9


def test_undeliverable_crossing_still_tracks_hysteresis():
    # The internal "elevated" state should still track risk_alert_threshold/
    # risk_clear_threshold even while nothing is deliverable, so a dip below
    # risk_clear_threshold still clears it (no permanently-stuck state).
    agg = RiskAggregator(make_settings(risk_alert_threshold=0.6, alert_min_score=0.8))
    assert agg.update("SOL-PERP", 0.0, [det("a", 0.65)]) is None
    assert agg.update("SOL-PERP", 1.0, [det("a", 0.2)]) is None  # drops below clear
    assert agg.score("SOL-PERP") < 0.4
    # A fresh deliverable spike after clearing fires right away.
    out = agg.update("SOL-PERP", 2.0, [det("a", 0.9)])
    assert out is not None
