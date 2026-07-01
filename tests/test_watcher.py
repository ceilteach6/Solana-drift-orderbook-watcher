"""
tests/test_watcher.py

Regression test for the detector-stack-failure escalation added to
Watcher._tick: when every detector raises for several consecutive ticks on a
market, that must surface as a loud, one-time alert rather than silently
decaying into "risk 0.0 / calm market" forever (see
Watcher._track_detector_stack_health).
"""

from unittest.mock import MagicMock

from config.settings import Settings
from src.watcher import _DETECTOR_STACK_FAILURE_ALERT_THRESHOLD, Watcher


def make_watcher(**overrides):
    overrides.setdefault("markets", ["TEST-PERP"])
    settings = Settings(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        **overrides,
    )
    watcher = Watcher(settings)
    watcher.alert.emit = MagicMock(return_value=0)
    return watcher


def test_no_alert_below_threshold():
    watcher = make_watcher()
    total = len(watcher.detectors)
    for _ in range(_DETECTOR_STACK_FAILURE_ALERT_THRESHOLD - 1):
        watcher._track_detector_stack_health("TEST-PERP", total)
    watcher.alert.emit.assert_not_called()


def test_alert_fires_once_at_threshold_and_stays_quiet_while_elevated():
    watcher = make_watcher()
    total = len(watcher.detectors)
    for _ in range(_DETECTOR_STACK_FAILURE_ALERT_THRESHOLD):
        watcher._track_detector_stack_health("TEST-PERP", total)
    assert watcher.alert.emit.call_count == 1
    (detections,) = watcher.alert.emit.call_args[0]
    assert detections[0].detector == "detector_stack_failure"
    assert detections[0].market == "TEST-PERP"
    assert detections[0].score == 1.0

    # Continued failures don't re-spam the alert.
    for _ in range(5):
        watcher._track_detector_stack_health("TEST-PERP", total)
    assert watcher.alert.emit.call_count == 1


def test_recovery_resets_and_allows_a_future_alert():
    watcher = make_watcher()
    total = len(watcher.detectors)
    for _ in range(_DETECTOR_STACK_FAILURE_ALERT_THRESHOLD):
        watcher._track_detector_stack_health("TEST-PERP", total)
    assert watcher.alert.emit.call_count == 1

    watcher._track_detector_stack_health("TEST-PERP", 0)  # a tick with a success

    for _ in range(_DETECTOR_STACK_FAILURE_ALERT_THRESHOLD):
        watcher._track_detector_stack_health("TEST-PERP", total)
    assert watcher.alert.emit.call_count == 2


def test_partial_failure_does_not_count_toward_the_streak():
    watcher = make_watcher()
    total = len(watcher.detectors)
    assert total > 1
    for _ in range(20):
        watcher._track_detector_stack_health("TEST-PERP", total - 1)  # one detector still works
    watcher.alert.emit.assert_not_called()


def test_markets_are_tracked_independently():
    watcher = make_watcher(markets=["A-PERP", "B-PERP"])
    total = len(watcher.detectors)
    for _ in range(_DETECTOR_STACK_FAILURE_ALERT_THRESHOLD):
        watcher._track_detector_stack_health("A-PERP", total)
    assert watcher.alert.emit.call_count == 1
    (detections,) = watcher.alert.emit.call_args[0]
    assert detections[0].market == "A-PERP"

    watcher._track_detector_stack_health("B-PERP", 0)
    watcher.alert.emit.assert_called_once()  # unaffected by the other market
