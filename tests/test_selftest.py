"""
tests/test_selftest.py

Verifies the algorithmic self-test: every injected scenario must fire its
target detector (and the risk aggregator) under the *default* configuration.
This guards against a detector silently breaking or a threshold drifting so far
that the known-positive scenarios stop triggering.
"""

from config.settings import load_settings
from src.selftest import run_selftest, selftest_main, _SCENARIOS


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


def test_run_selftest_survives_a_broken_detector(monkeypatch):
    """A custom detector that raises must fail its check, not crash the run.

    run_selftest() also backs the live periodic health-check (src/watcher.py),
    which has no surrounding try/except -- so an uncaught exception here would
    take down the whole watcher process, not just one check.
    """

    class BrokenDetector:
        name = "broken"

        def __init__(self, settings):
            pass

        def analyze(self, snapshot, history):
            raise RuntimeError("boom")

    monkeypatch.setattr("src.selftest.DEFAULT_DETECTORS", (BrokenDetector,))
    monkeypatch.setattr("src.selftest._SCENARIOS", {"broken": _SCENARIOS["flicker"]})

    results = run_selftest(load_settings())  # must not raise
    assert all(not r.passed for r in results)
    assert any("boom" in r.note for r in results)
