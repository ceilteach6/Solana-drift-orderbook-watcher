"""
tests/test_watcher.py

Regression tests for the orchestrator's structural fixes:
- the tick interval is derived once and reused everywhere (no drift between
  __init__'s clamped value and a second, unclamped derivation in the run loop)
- per-market history is sized from the *longest* detector lookback window
  (flicker and spoof-pull), not just flicker's
- repeated missed/failed snapshots trigger a feed reconnect instead of
  silently ticking forever against a stale/dead feed
"""

import asyncio
from types import SimpleNamespace

import pytest

import src.watcher as watcher_mod
from src.collector.orderbook_feed import OrderbookSnapshot
from src.watcher import Watcher


def make_settings(**overrides):
    base = dict(
        markets=("SOL-PERP",),
        update_frequency_ms=1000,
        flicker_window_sec=5.0,
        spoof_window_sec=10.0,
        risk_aggregation=False,
        storage_enabled=False,
        healthcheck_enabled=False,
        alert_min_score=0.6,
        alert_format="console",
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="",
        run_duration_sec=0.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FeedStub:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot
        self.closed = False

    async def get_snapshot(self, market):
        return self._snapshot

    async def close(self):
        self.closed = True


def test_history_length_covers_longest_detector_window():
    # interval = 1s, longest window = spoof's 10s (longer than flicker's 5s).
    settings = make_settings(update_frequency_ms=1000, flicker_window_sec=5.0, spoof_window_sec=10.0)
    watcher = Watcher(settings)
    history_len = watcher._history["SOL-PERP"].maxlen
    # Must retain enough snapshots to cover the 10s spoof-pull window, not
    # just flicker's 5s (which would previously undersize this to ~9).
    assert history_len >= int(10.0 / 1.0)
    assert history_len == max(8, int(10.0 / 1.0) + 4)


def test_interval_computed_once_matches_clamped_formula():
    settings = make_settings(update_frequency_ms=250)
    watcher = Watcher(settings)
    assert watcher._interval == 0.25


def test_interval_clamped_for_pathological_update_frequency():
    # UPDATE_FREQUENCY_MS=0 must not produce a zero-second sleep (a busy loop
    # hammering the feed); it's clamped to a minimum.
    settings = make_settings(update_frequency_ms=0)
    watcher = Watcher(settings)
    assert watcher._interval == 0.001


def test_run_loop_sleeps_for_the_single_source_of_truth_interval(monkeypatch):
    settings = make_settings(update_frequency_ms=250)
    watcher = Watcher(settings)
    watcher.feed = _FeedStub(snapshot=None)

    captured = []

    async def fake_sleep(seconds):
        captured.append(seconds)
        raise RuntimeError("stop-after-first-sleep")

    monkeypatch.setattr(watcher_mod.asyncio, "sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop-after-first-sleep"):
        asyncio.run(watcher._run_loop())

    assert captured == [watcher._interval] == [0.25]


def test_repeated_misses_trigger_feed_reconnect(monkeypatch):
    settings = make_settings()
    watcher = Watcher(settings)
    old_feed = _FeedStub(snapshot=None)
    watcher.feed = old_feed

    new_feed = _FeedStub(snapshot=None)

    async def fake_create_feed(_settings):
        return new_feed

    monkeypatch.setattr(watcher_mod, "create_feed", fake_create_feed)

    async def run():
        # One below the threshold: no reconnect yet.
        for _ in range(watcher_mod._RECONNECT_FAILURE_THRESHOLD - 1):
            await watcher._tick("SOL-PERP")
        assert watcher.feed is old_feed
        assert not old_feed.closed

        # Crossing the threshold triggers a reconnect.
        await watcher._tick("SOL-PERP")
        assert watcher.feed is new_feed
        assert old_feed.closed
        assert watcher._consecutive_failures.get("SOL-PERP", 0) == 0

    asyncio.run(run())


def test_successful_snapshot_resets_failure_counter():
    settings = make_settings()
    watcher = Watcher(settings)
    watcher._consecutive_failures["SOL-PERP"] = 3

    snapshot = OrderbookSnapshot(market="SOL-PERP", timestamp=1.0, bids=[], asks=[])
    watcher.feed = _FeedStub(snapshot=snapshot)

    asyncio.run(watcher._tick("SOL-PERP"))
    assert watcher._consecutive_failures["SOL-PERP"] == 0
