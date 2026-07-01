"""
tests/test_watcher.py

Unit tests for small pure helpers in src.watcher (not the full Watcher
orchestration loop, which needs a live/synthetic feed).
"""

from types import SimpleNamespace

from src.watcher import history_length


def settings(**overrides):
    base = dict(
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        spoof_window_sec=10.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_history_length_covers_the_wider_of_flicker_and_spoof_windows():
    # Regression: history_length used to be sized from flicker_window_sec
    # alone, even though spoof_pull's window (10s by default) is wider —
    # silently truncating the history spoof_pull needs.
    n = history_length(settings(flicker_window_sec=5.0, spoof_window_sec=10.0))
    interval_sec = 1.0
    assert n >= (10.0 / interval_sec) + 4


def test_history_length_covers_flicker_when_it_is_the_wider_window():
    n = history_length(settings(flicker_window_sec=20.0, spoof_window_sec=10.0))
    assert n >= (20.0 / 1.0) + 4


def test_history_length_has_a_floor():
    n = history_length(settings(flicker_window_sec=0.1, spoof_window_sec=0.1))
    assert n >= 8
