"""
tests/test_watcher.py

Unit tests for small pure helpers in src.watcher (not the full Watcher
orchestration loop, which needs a live/synthetic feed).
"""

import asyncio
import threading
import time
from types import SimpleNamespace

from src.watcher import Watcher, history_length, stale_feed_after_sec


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


def test_stale_feed_after_sec_absorbs_a_few_timed_out_polls():
    # Must comfortably exceed a handful of snapshot_timeout_sec-bounded
    # failures so ordinary jitter/timeouts don't trip a false liveness alert.
    n = stale_feed_after_sec(settings(snapshot_timeout_sec=5.0))
    assert n >= 3 * 5.0


def test_stale_feed_after_sec_has_a_floor_for_fast_polling():
    n = stale_feed_after_sec(settings(update_frequency_ms=10, snapshot_timeout_sec=0.01))
    assert n >= 30.0


class _RecordingAlert:
    def __init__(self):
        self.emitted = []

    def emit(self, detections):
        self.emitted.extend(detections)


def test_check_liveness_alerts_once_when_a_market_goes_stale():
    # Regression: the periodic health-check only replays canned scenarios
    # through the detectors — it never proves get_snapshot() itself is still
    # succeeding, so a hung feed produced zero alerts forever. _check_liveness
    # is the independent signal that catches that.
    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(markets=("SOL-PERP",))
    watcher.alert = _RecordingAlert()
    watcher._stale_after = 10.0
    watcher._started_at = time.monotonic() - 100.0  # long past the threshold
    watcher._last_ok = {}
    watcher._stale_alerted = set()

    watcher._check_liveness()
    assert len(watcher.alert.emitted) == 1
    assert watcher.alert.emitted[0].detector == "liveness"

    # Stays quiet on the next call instead of re-alerting every loop tick.
    watcher._check_liveness()
    assert len(watcher.alert.emitted) == 1


def test_check_liveness_clears_after_a_fresh_snapshot():
    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(markets=("SOL-PERP",))
    watcher.alert = _RecordingAlert()
    watcher._stale_after = 10.0
    watcher._started_at = time.monotonic() - 100.0
    watcher._last_ok = {}
    watcher._stale_alerted = set()

    watcher._check_liveness()
    assert len(watcher.alert.emitted) == 1

    watcher._last_ok["SOL-PERP"] = time.monotonic()
    watcher._check_liveness()
    assert "SOL-PERP" not in watcher._stale_alerted

    # A later relapse must alert again, not stay silently suppressed forever.
    watcher._last_ok["SOL-PERP"] = time.monotonic() - 100.0
    watcher._check_liveness()
    assert len(watcher.alert.emitted) == 2


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
    watcher._last_ok = {}
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
