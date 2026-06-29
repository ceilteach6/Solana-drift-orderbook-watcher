"""
tests/test_selftest.py

Verifies the algorithmic self-test: every injected scenario must fire its
target detector (and the risk aggregator) under the *default* configuration.
This guards against a detector silently breaking or a threshold drifting so far
that the known-positive scenarios stop triggering.
"""

from config.settings import load_settings
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
