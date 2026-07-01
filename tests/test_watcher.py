"""
tests/test_watcher.py

Guards the per-market history buffer sizing in src/watcher.py: it must stay
bounded even when FLICKER_WINDOW_SEC / UPDATE_FREQUENCY_MS are configured to
values that would otherwise imply a multi-million-entry deque (and gigabytes
of memory) per market.
"""

from config.settings import load_settings
from src.watcher import Watcher


def test_history_length_is_capped_for_extreme_config(monkeypatch):
    monkeypatch.setenv("FLICKER_WINDOW_SEC", "3600")
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "1")
    settings = load_settings()

    watcher = Watcher(settings)

    for history in watcher._history.values():
        assert history.maxlen <= 5000


def test_history_length_still_covers_flicker_window_normally():
    settings = load_settings()
    watcher = Watcher(settings)

    interval = settings.update_frequency_ms / 1000
    expected = max(8, int(settings.flicker_window_sec / interval) + 4)
    for history in watcher._history.values():
        assert history.maxlen == expected
