"""
tests/test_watcher_history.py

Regression test: the per-market history deque must be sized to cover the
longest lookback window any detector needs, not just the flicker window.
Network-free (storage/alerting left at their off-by-default settings).
"""

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(rpc_url="http://localhost", drift_env="devnet", keypair_path="", markets=("SOL-PERP",))
    base.update(overrides)
    return Settings(**base)


def test_history_len_covers_the_longest_detector_window():
    # spoof_pull's 10s window is longer than flicker's 5s window (the
    # defaults); the deque must retain at least 10s of snapshots, not 5s.
    settings = make_settings(
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        spoof_window_sec=10.0,
    )
    watcher = Watcher(settings)
    history_len = watcher._history["SOL-PERP"].maxlen
    assert history_len >= 10 + 1, (
        f"history_len={history_len} does not cover the 10s spoof_pull window"
    )


def test_history_len_covers_flicker_when_it_is_the_longer_window():
    settings = make_settings(
        update_frequency_ms=1000,
        flicker_window_sec=20.0,
        spoof_window_sec=5.0,
    )
    watcher = Watcher(settings)
    history_len = watcher._history["SOL-PERP"].maxlen
    assert history_len >= 20 + 1
