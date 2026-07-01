"""
tests/test_alert.py

Unit tests for AlertDispatcher's score gating, including the
``bypass_score_filter`` escape hatch used for risk-aggregator alerts (which
are already gated by their own threshold/hysteresis and must not be silently
dropped by an independently configured ALERT_MIN_SCORE).
"""

from types import SimpleNamespace

from src.alert.base import Alert, AlertDispatcher
from src.detector.base import Detection


class RecordingSink(Alert):
    name = "recording"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.delivered: list[Detection] = []

    def deliver(self, detection) -> None:
        self.delivered.append(detection)


def make_settings(**overrides):
    base = dict(alert_min_score=0.6)
    base.update(overrides)
    return SimpleNamespace(**base)


def det(score, detector="risk", market="SOL-PERP"):
    return Detection(detector=detector, market=market, score=score, message="x")


def test_emit_filters_below_min_score():
    settings = make_settings(alert_min_score=0.6)
    sink = RecordingSink(settings)
    dispatcher = AlertDispatcher(settings, [sink])

    emitted = dispatcher.emit([det(0.3)])

    assert emitted == 0
    assert sink.delivered == []


def test_emit_delivers_at_or_above_min_score():
    settings = make_settings(alert_min_score=0.6)
    sink = RecordingSink(settings)
    dispatcher = AlertDispatcher(settings, [sink])

    emitted = dispatcher.emit([det(0.6)])

    assert emitted == 1
    assert len(sink.delivered) == 1


def test_bypass_score_filter_delivers_below_min_score():
    # A risk-aggregator alert (e.g. score 0.3, because RISK_ALERT_THRESHOLD was
    # lowered independently of ALERT_MIN_SCORE) must still reach the sinks.
    settings = make_settings(alert_min_score=0.6)
    sink = RecordingSink(settings)
    dispatcher = AlertDispatcher(settings, [sink])

    emitted = dispatcher.emit([det(0.3)], bypass_score_filter=True)

    assert emitted == 1
    assert len(sink.delivered) == 1
