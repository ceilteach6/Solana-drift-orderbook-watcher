"""
tests/test_watcher.py

Unit tests for small pure helpers in src.watcher (not the full Watcher
orchestration loop, which needs a live/synthetic feed).
"""

import asyncio
import threading
import time
from types import SimpleNamespace

from src.collector.orderbook_feed import DriftOrderbookFeed
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


class _NullAlert:
    def emit(self, detections):
        pass


class _RecordingAlert:
    def __init__(self):
        self.detections = []

    def emit(self, detections):
        self.detections.extend(detections)


def _fake_drift_feed(monkeypatch, *, connect_ok=True):
    # DriftOrderbookFeed.connect() normally imports driftpy and dials a live
    # RPC; patch it out so reconnect logic can be exercised without a
    # network. Patched at the class level so both the "old" feed and any
    # "new" feed _reconnect_feed constructs behave identically.
    async def fake_connect(self):
        if not connect_ok:
            raise RuntimeError("simulated connect failure")
        self._stack = SimpleNamespace(close=_noop_async)

    monkeypatch.setattr(DriftOrderbookFeed, "connect", fake_connect)
    return DriftOrderbookFeed(SimpleNamespace(markets=("SOL-PERP",)))


async def _noop_async():
    return None


def test_tick_resets_feed_failure_counter_and_marks_success():
    watcher = object.__new__(Watcher)
    watcher.feed = _EmptyFeed()
    watcher.detectors = []
    watcher._history = {"SOL-PERP": []}
    watcher.aggregator = None
    watcher.alert = _NullAlert()
    watcher.store = None
    watcher.settings = SimpleNamespace(persist_snapshots=False)
    watcher._feed_consecutive_failures = 2
    watcher._last_snapshot_ok = 0.0

    asyncio.run(watcher._tick("SOL-PERP"))

    assert watcher._feed_consecutive_failures == 0
    assert watcher._last_snapshot_ok > 0.0


def test_tick_counts_failures_and_resets_after_reconnect_attempt():
    # Regression: a snapshot exception/None used to be caught and logged
    # with no other consequence, so an RPC that never recovers polled
    # forever with nothing to reconnect it and nothing to tell an operator
    # relying on webhook/Telegram alerts (rather than console logs).
    class _FailingFeed:
        async def get_snapshot(self, market):
            raise RuntimeError("simulated RPC failure")

    watcher = object.__new__(Watcher)
    watcher.feed = _FailingFeed()  # not a DriftOrderbookFeed -> reconnect no-ops
    watcher.settings = SimpleNamespace(
        feed_max_consecutive_failures=2, persist_snapshots=False, markets=("SOL-PERP",)
    )
    watcher.alert = _NullAlert()
    watcher._feed_consecutive_failures = 0
    watcher._last_snapshot_ok = 0.0

    async def run_two_ticks():
        await watcher._tick("SOL-PERP")
        assert watcher._feed_consecutive_failures == 1
        await watcher._tick("SOL-PERP")

    asyncio.run(run_two_ticks())

    # Threshold hit on the 2nd failure -> counter resets even though the
    # feed itself isn't a DriftOrderbookFeed and reconnect is a no-op.
    assert watcher._feed_consecutive_failures == 0


def test_reconnect_replaces_a_dead_drift_feed_after_failure_threshold(monkeypatch):
    old_feed = _fake_drift_feed(monkeypatch)

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(
        feed_max_consecutive_failures=3, markets=("SOL-PERP",)
    )
    watcher.feed = old_feed
    watcher.alert = _NullAlert()
    watcher._feed_consecutive_failures = 0
    watcher._last_snapshot_ok = 0.0

    async def run():
        for _ in range(3):
            await watcher._note_feed_failure()

    asyncio.run(run())

    assert watcher.feed is not old_feed
    assert isinstance(watcher.feed, DriftOrderbookFeed)
    assert watcher._feed_consecutive_failures == 0
    assert watcher._last_snapshot_ok > 0.0


def test_reconnect_keeps_old_feed_and_alerts_when_reconnect_fails(monkeypatch):
    old_feed = _fake_drift_feed(monkeypatch, connect_ok=False)

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(
        feed_max_consecutive_failures=1, markets=("SOL-PERP",)
    )
    watcher.feed = old_feed
    watcher.alert = _RecordingAlert()
    watcher._feed_consecutive_failures = 0
    watcher._last_snapshot_ok = 0.0

    asyncio.run(watcher._note_feed_failure())

    # Reconnect failed -> keep the old (still broken, but known) feed rather
    # than silently swapping to a fresh half-built one, and tell someone.
    assert watcher.feed is old_feed
    assert len(watcher.alert.detections) == 1
    assert "reconnect failed" in watcher.alert.detections[0].message.lower()


def test_reconnect_is_a_noop_for_a_non_drift_feed():
    class _SyntheticStub:
        pass

    stub = _SyntheticStub()
    watcher = object.__new__(Watcher)
    watcher.feed = stub
    watcher.settings = SimpleNamespace(feed_max_consecutive_failures=1)
    watcher.alert = None  # must not be touched

    asyncio.run(watcher._reconnect_feed())

    assert watcher.feed is stub


def test_healthcheck_alerts_when_feed_has_not_succeeded_recently(monkeypatch):
    import src.watcher as watcher_module

    monkeypatch.setattr(watcher_module, "run_selftest", lambda settings: [])

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(
        healthcheck_enabled=True, healthcheck_interval_sec=0.0, feed_stale_after_sec=10.0,
    )
    watcher.alert = _RecordingAlert()
    watcher._last_healthcheck = 0.0
    watcher._last_snapshot_ok = time.monotonic() - 100.0

    watcher._maybe_healthcheck()

    assert any("stale" in d.message.lower() for d in watcher.alert.detections)


def test_healthcheck_does_not_alert_when_feed_is_fresh(monkeypatch):
    import src.watcher as watcher_module

    monkeypatch.setattr(watcher_module, "run_selftest", lambda settings: [])

    watcher = object.__new__(Watcher)
    watcher.settings = SimpleNamespace(
        healthcheck_enabled=True, healthcheck_interval_sec=0.0, feed_stale_after_sec=10.0,
    )
    watcher.alert = _RecordingAlert()
    watcher._last_healthcheck = 0.0
    watcher._last_snapshot_ok = time.monotonic()

    watcher._maybe_healthcheck()

    assert watcher.alert.detections == []
