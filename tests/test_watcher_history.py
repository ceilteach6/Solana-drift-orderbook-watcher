"""
tests/test_watcher_history.py

The per-market history deque must retain enough snapshots to cover every
detector's lookback window. Sizing it from flicker_window_sec alone (the
original bug) silently truncated spoof_pull's longer window whenever
spoof_window_sec > flicker_window_sec (the defaults: 10s vs 5s).
"""

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(
        rpc_url="https://example.invalid",
        drift_env="mainnet",
        keypair_path="",
        markets=("SOL-PERP",),
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        spoof_window_sec=10.0,
        risk_aggregation=False,
        storage_enabled=False,
        healthcheck_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def test_history_covers_the_longest_detector_window():
    settings = make_settings()
    watcher = Watcher(settings)
    history = watcher._history["SOL-PERP"]

    interval_sec = settings.update_frequency_ms / 1000
    # Must retain at least spoof_window_sec worth of snapshots, not just
    # flicker_window_sec's (shorter) worth.
    assert history.maxlen >= settings.spoof_window_sec / interval_sec


def test_history_covers_flicker_when_it_is_the_longer_window():
    settings = make_settings(flicker_window_sec=20.0, spoof_window_sec=5.0)
    watcher = Watcher(settings)
    history = watcher._history["SOL-PERP"]

    interval_sec = settings.update_frequency_ms / 1000
    assert history.maxlen >= settings.flicker_window_sec / interval_sec
