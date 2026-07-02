"""
tests/test_watcher.py

Unit tests for small pure helpers in src.watcher (not the full Watcher
orchestration loop, which needs a live/synthetic feed).
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

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


class _BoomAggregator:
    def update(self, market, timestamp, detections):
        raise TypeError("simulated malformed Detection")


class _BoomAlert:
    def emit(self, detections):
        raise TypeError("simulated broken alert sink")


class _EmptyFeed:
    async def get_snapshot(self, market):
        return SimpleNamespace(mid=100.0)


def test_tick_survives_a_broken_aggregator_or_alert_sink():
    # Regression: an exception raised while aggregating risk or emitting an
    # alert (e.g. a malformed Detection with a non-comparable score) used to
    # propagate straight out of _tick(), through _run_loop()'s bare while
    # True, and crash the entire watcher process — taking every market down
    # over a single bad detection instead of just skipping that alert.
    watcher = object.__new__(Watcher)
    watcher.feed = _EmptyFeed()
    watcher.detectors = []
    watcher._history = {"SOL-PERP": []}
    watcher.aggregator = _BoomAggregator()
    watcher.alert = _BoomAlert()
    watcher.store = None
    watcher.settings = SimpleNamespace(persist_snapshots=False)

    # Must not raise.
    asyncio.run(watcher._tick("SOL-PERP"))


def test_healthcheck_survives_a_broken_alert_sink(monkeypatch):
    # Same failure mode as above, via the periodic self-test health-check
    # path instead of the per-tick alert path.
    import src.watcher as watcher_module

    monkeypatch.setattr(
        watcher_module, "run_selftest",
        lambda settings: [SimpleNamespace(name="flicker", passed=False)],
    )

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(
        healthcheck_enabled=True, healthcheck_interval_sec=0.0,
    )
    watcher.alert = _BoomAlert()
    watcher._last_healthcheck = 0.0

    # Must not raise.
    watcher._maybe_healthcheck()


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


class _RecordingFeed:
    def __init__(self):
        self.closed = False

    async def get_snapshot(self, market):
        return None

    async def close(self):
        self.closed = True


class _RecordingAlertDispatcher:
    def __init__(self):
        self.closed = False
        self.sinks = []

    def close(self):
        self.closed = True


class _RecordingSQLiteStore:
    def __init__(self):
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True

    def close(self):
        self.closed = True


def test_start_closes_feed_alert_and_store_on_clean_stop(monkeypatch):
    # Regression: the webhook alert sink's delivery pool was never shut down
    # anywhere, which could hold the process open at exit behind a queued
    # backlog. start() must close every resource it opened, every time —
    # not just the feed and store.
    import src.watcher as watcher_module

    feed = _RecordingFeed()
    monkeypatch.setattr(watcher_module, "create_feed", lambda settings: _async_return(feed))

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(
        markets=["SOL-PERP"], update_frequency_ms=1000, run_duration_sec=0.001,
        healthcheck_enabled=False,
    )
    watcher.detectors = []
    watcher.aggregator = None
    watcher.alert = _RecordingAlertDispatcher()
    watcher.store = _RecordingSQLiteStore()
    watcher.feed = None
    watcher._last_healthcheck = 0.0
    watcher._history = {"SOL-PERP": []}
    watcher._banner = lambda: None

    asyncio.run(watcher.start())

    assert feed.closed
    assert watcher.alert.closed
    assert watcher.store.closed


def test_start_still_closes_store_and_alert_if_feed_creation_fails(monkeypatch):
    # Regression: store.connect() and create_feed() used to run before the
    # try/finally, so a cancelled/failed create_feed() left the store
    # connection (and any already-open sinks) never closed.
    import src.watcher as watcher_module

    async def _boom(settings):
        raise RuntimeError("simulated feed creation failure")

    monkeypatch.setattr(watcher_module, "create_feed", _boom)

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(markets=["SOL-PERP"])
    watcher.alert = _RecordingAlertDispatcher()
    watcher.store = _RecordingSQLiteStore()
    watcher.feed = None
    watcher._banner = lambda: None

    with pytest.raises(RuntimeError, match="simulated feed creation failure"):
        asyncio.run(watcher.start())

    assert watcher.store.connected
    assert watcher.store.closed
    assert watcher.alert.closed


async def _async_return(value):
    return value
