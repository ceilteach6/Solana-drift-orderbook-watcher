"""
tests/test_selftest.py

Verifies the algorithmic self-test: every injected scenario must fire its
target detector (and the risk aggregator) under the *default* configuration.
This guards against a detector silently breaking or a threshold drifting so far
that the known-positive scenarios stop triggering.
"""

from config.settings import load_settings
from src.detector.repeated_size import RepeatedSizeDetector
from src.selftest import run_selftest, selftest_main


def test_all_scenarios_fire_under_default_settings():
    results = run_selftest(load_settings())
    failed = [r.name for r in results if not r.passed]
    assert not failed, f"self-test scenarios did not fire: {failed}"
    # Expect all five detectors + the risk aggregator.
    names = {r.name for r in results}
    assert names == {
        "repeated_size",
        "layering",
        "flicker",
        "imbalance",
        "spoof_pull",
        "risk-aggregator",
    }


def test_selftest_main_returns_zero_on_success():
    assert selftest_main(load_settings()) == 0


def test_a_broken_detector_fails_its_own_check_without_crashing(monkeypatch):
    # This runs from the live periodic health-check (src/watcher.py), so a
    # detector raising must be reported as a failed check, not propagate and
    # take down the whole watcher loop.
    def boom(self, snapshot, history):
        raise RuntimeError("boom")

    monkeypatch.setattr(RepeatedSizeDetector, "analyze", boom)

    results = run_selftest(load_settings())
    by_name = {r.name: r for r in results}
    assert by_name["repeated_size"].passed is False
    assert "boom" in by_name["repeated_size"].note
    # Other detectors still ran normally.
    assert by_name["imbalance"].passed is True
