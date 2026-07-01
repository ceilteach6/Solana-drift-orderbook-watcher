"""
tests/test_watcher.py

Unit tests for small pure helpers in src.watcher (not the full Watcher
orchestration loop, which needs a live/synthetic feed).
"""

import asyncio
import threading
from types import SimpleNamespace

from src.watcher import Watcher, history_length


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


class _RecordingStore:
    def __init__(self):
        self.calls = []

    def record_tick(self, market, snapshot, detections, risk, *, persist_snapshot):
        self.calls.append(threading.get_ident())


def test_persist_runs_the_blocking_store_write_off_the_event_loop():
    # Regression: _persist used to call store.record_tick() directly inside
    # the async _tick(), so its blocking commit()/fsync ran on the event loop
    # thread and stalled polling for every other market during the write.
    watcher = object.__new__(Watcher)
    watcher.store = _RecordingStore()
    watcher.aggregator = None
    watcher.settings = SimpleNamespace(persist_snapshots=False)

    main_thread = threading.get_ident()
    assert asyncio.iscoroutinefunction(Watcher._persist)
    asyncio.run(watcher._persist("SOL-PERP", SimpleNamespace(mid=100.0), []))

    assert len(watcher.store.calls) == 1
    assert watcher.store.calls[0] != main_thread
