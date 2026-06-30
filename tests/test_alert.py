"""
tests/test_alert.py

Unit tests for AlertDispatcher: score filtering and the per-(market,
detector) cooldown that protects sinks (webhook/Telegram) from bursty raw
detections. Network-free — uses a fake in-memory sink.
"""

from types import SimpleNamespace

from src.alert.base import Alert, AlertDispatcher
from src.detector.base import Detection


def make_settings(**overrides):
    base = dict(alert_min_score=0.6, alert_cooldown_sec=5.0)
    base.update(overrides)
    return SimpleNamespace(**base)


class RecordingSink(Alert):
    name = "recording"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self.delivered = []

    def deliver(self, detection) -> None:
        self.delivered.append(detection)


def det(detector="repeated_size", score=0.9, market="SOL-PERP"):
    return Detection(detector=detector, market=market, score=score, message="x")


def test_low_score_is_filtered():
    sink = RecordingSink(make_settings())
    dispatcher = AlertDispatcher(make_settings(), [sink])
    dispatcher.emit([det(score=0.1)])
    assert sink.delivered == []


def test_first_alert_for_a_key_always_delivers():
    sink = RecordingSink(make_settings())
    dispatcher = AlertDispatcher(make_settings(), [sink])
    emitted = dispatcher.emit([det()])
    assert emitted == 1
    assert len(sink.delivered) == 1


def test_repeat_within_cooldown_is_suppressed():
    sink = RecordingSink(make_settings())
    dispatcher = AlertDispatcher(make_settings(alert_cooldown_sec=5.0), [sink])
    dispatcher.emit([det()])
    dispatcher.emit([det()])  # same (market, detector), immediately after
    assert len(sink.delivered) == 1


def test_different_detector_or_market_is_not_suppressed():
    sink = RecordingSink(make_settings())
    dispatcher = AlertDispatcher(make_settings(), [sink])
    dispatcher.emit([det(detector="repeated_size")])
    dispatcher.emit([det(detector="layering")])
    dispatcher.emit([det(market="BTC-PERP")])
    assert len(sink.delivered) == 3


def test_cooldown_disabled_when_zero():
    sink = RecordingSink(make_settings())
    dispatcher = AlertDispatcher(make_settings(alert_cooldown_sec=0), [sink])
    dispatcher.emit([det()])
    dispatcher.emit([det()])
    assert len(sink.delivered) == 2


def test_sink_exception_does_not_block_other_sinks():
    class BrokenSink(Alert):
        name = "broken"

        def deliver(self, detection) -> None:
            raise RuntimeError("boom")

    good = RecordingSink(make_settings())
    dispatcher = AlertDispatcher(make_settings(), [BrokenSink(make_settings()), good])
    emitted = dispatcher.emit([det()])
    assert emitted == 1
    assert len(good.delivered) == 1
