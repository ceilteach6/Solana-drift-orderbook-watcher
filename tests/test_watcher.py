"""
tests/test_watcher.py

Regression coverage for Watcher's per-market history buffer sizing: it must
cover every registered detector's declared look-back window (see
BaseDetector.required_window_sec), not just the flicker window, so a
detector configured with the largest window can't have its own data
silently truncated by ``deque(maxlen=...)``.
"""

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(
        rpc_url="",
        drift_env="mainnet",
        keypair_path="",
        markets=["SOL-PERP"],
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        spoof_window_sec=10.0,
        risk_aggregation=False,
        storage_enabled=False,
        healthcheck_enabled=False,
        alert_format="console",
    )
    base.update(overrides)
    return Settings(**base)


def test_history_covers_the_largest_detector_window():
    # spoof_window_sec (10s) exceeds flicker_window_sec (5s); the buffer must
    # be sized from the max of the two, not just the flicker window.
    settings = make_settings(flicker_window_sec=5.0, spoof_window_sec=10.0)
    watcher = Watcher(settings)
    history = watcher._history["SOL-PERP"]
    assert history.maxlen >= int(10.0 / 1.0) + 4


def test_history_grows_when_spoof_window_is_raised_past_flicker_window():
    small = Watcher(make_settings(flicker_window_sec=5.0, spoof_window_sec=5.0))
    large = Watcher(make_settings(flicker_window_sec=5.0, spoof_window_sec=60.0))
    assert large._history["SOL-PERP"].maxlen > small._history["SOL-PERP"].maxlen
