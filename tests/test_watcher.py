"""
tests/test_watcher.py

Tests Watcher internals that don't require a live feed: history sizing and
the health-check's crash isolation.
"""

from config.settings import Settings
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(rpc_url="x", drift_env="mainnet", keypair_path="", markets=["SOL-PERP"])
    base.update(overrides)
    return Settings(**base)


def test_history_buffer_covers_the_longer_of_flicker_and_spoof_windows():
    settings = make_settings(
        update_frequency_ms=1000, flicker_window_sec=5.0, spoof_window_sec=10.0,
    )
    watcher = Watcher(settings)
    history = watcher._history["SOL-PERP"]
    # interval = 1s, window = max(5, 10) = 10s -> at least 10 slots retained.
    assert history.maxlen >= 10 + 4


def test_history_buffer_shrinks_to_flicker_when_it_is_the_longer_window():
    settings = make_settings(
        update_frequency_ms=1000, flicker_window_sec=20.0, spoof_window_sec=10.0,
    )
    watcher = Watcher(settings)
    assert watcher._history["SOL-PERP"].maxlen >= 20 + 4


def test_healthcheck_does_not_crash_the_loop_when_selftest_raises(monkeypatch):
    settings = make_settings(healthcheck_enabled=True, healthcheck_interval_sec=0)
    watcher = Watcher(settings)

    def boom(_settings):
        raise RuntimeError("synthetic detector regression")

    monkeypatch.setattr("src.watcher.run_selftest", boom)

    # Must not raise — a health-check failure is a logged diagnostic, not a
    # reason to take down the live watcher.
    watcher._maybe_healthcheck()


def test_maybe_prune_noop_without_storage():
    settings = make_settings(storage_enabled=False)
    watcher = Watcher(settings)
    # No store configured -> must be a safe no-op, not an AttributeError.
    watcher._maybe_prune()
