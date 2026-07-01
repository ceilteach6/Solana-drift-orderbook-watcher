"""
tests/test_watcher.py

Unit tests for Watcher's per-market history sizing. Network-free — only
exercises __init__, never start()/the poll loop.
"""

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(rpc_url="", drift_env="mainnet", keypair_path="", markets=("SOL-PERP",))
    base.update(overrides)
    return Settings(**base)


def test_history_covers_flicker_window_by_default():
    settings = make_settings(update_frequency_ms=1000, flicker_window_sec=5.0)
    w = Watcher(settings)
    assert w._history["SOL-PERP"].maxlen >= 5


def test_history_covers_spoof_window_when_longer_than_flicker():
    # spoof_pull.py filters `history` by spoof_window_sec (default 10s, twice
    # flicker's default 5s). Sizing the deque from flicker_window_sec alone
    # would silently cap spoof_pull's lookback below its configured window.
    settings = make_settings(
        update_frequency_ms=1000, flicker_window_sec=5.0, spoof_window_sec=30.0,
    )
    w = Watcher(settings)
    assert w._history["SOL-PERP"].maxlen >= 30


def test_history_covers_flicker_window_when_longer_than_spoof():
    settings = make_settings(
        update_frequency_ms=1000, flicker_window_sec=20.0, spoof_window_sec=5.0,
    )
    w = Watcher(settings)
    assert w._history["SOL-PERP"].maxlen >= 20
