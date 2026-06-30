"""
tests/test_feed_resilience.py

Covers the failure-handling structure around the live feed:
- create_feed() retries the real connection with backoff before giving up
- it still falls back to the synthetic feed once retries are exhausted
- detectors with a wider lookback window size the watcher's history buffer
  correctly (regression test for the flicker-only sizing bug)
"""

import asyncio
from types import SimpleNamespace

from config.settings import load_settings
from src.collector import orderbook_feed as obf
from src.detector.flicker import FlickerDetector
from src.detector.repeated_size import RepeatedSizeDetector
from src.detector.spoof_pull import SpoofPullDetector
from src.watcher import Watcher


async def _no_sleep(*_a, **_kw):
    return None


class _FlakyFeed(obf.OrderbookFeed):
    """Fails ``fail_times`` connect() calls, then succeeds."""

    fail_times = 0
    calls = 0

    def __init__(self, settings):
        self.settings = settings

    async def connect(self):
        type(self).calls += 1
        if type(self).calls <= type(self).fail_times:
            raise RuntimeError("transient connect failure")

    async def get_snapshot(self, market):
        return None


class _AlwaysFailFeed(obf.OrderbookFeed):
    calls = 0

    def __init__(self, settings):
        self.settings = settings

    async def connect(self):
        type(self).calls += 1
        raise RuntimeError("permanently down")


def test_create_feed_retries_transient_failure_before_succeeding(monkeypatch):
    _FlakyFeed.calls = 0
    _FlakyFeed.fail_times = 2
    monkeypatch.setattr(obf, "DriftOrderbookFeed", _FlakyFeed)
    monkeypatch.setattr(obf.asyncio, "sleep", _no_sleep)

    settings = SimpleNamespace(feed_connect_retries=3, markets=["SOL-PERP"])
    feed = asyncio.run(obf.create_feed(settings))

    assert _FlakyFeed.calls == 3  # 2 failures + 1 success, no premature fallback
    assert feed.is_demo is False


def test_create_feed_falls_back_to_synthetic_after_exhausting_retries(monkeypatch):
    _AlwaysFailFeed.calls = 0
    monkeypatch.setattr(obf, "DriftOrderbookFeed", _AlwaysFailFeed)
    monkeypatch.setattr(obf.asyncio, "sleep", _no_sleep)

    settings = SimpleNamespace(feed_connect_retries=3, markets=["SOL-PERP"])
    feed = asyncio.run(obf.create_feed(settings))

    assert _AlwaysFailFeed.calls == 3
    assert feed.is_demo is True


def test_required_history_sec_overrides():
    settings = SimpleNamespace(flicker_window_sec=7.0, spoof_window_sec=12.0)
    assert FlickerDetector.required_history_sec(settings) == 7.0
    assert SpoofPullDetector.required_history_sec(settings) == 12.0
    # Detectors that don't look at history default to 0.
    assert RepeatedSizeDetector.required_history_sec(settings) == 0.0


def test_watcher_history_covers_the_widest_detector_window(monkeypatch):
    monkeypatch.setenv("FLICKER_WINDOW_SEC", "5")
    monkeypatch.setenv("SPOOF_WINDOW_SEC", "20")
    monkeypatch.setenv("UPDATE_FREQUENCY_MS", "1000")
    settings = load_settings()

    watcher = Watcher(settings)
    history = watcher._history[settings.markets[0]]

    # 20s window / 1s interval must fit, not just the 5s flicker window.
    assert history.maxlen >= 20
