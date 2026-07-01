"""
tests/test_watcher.py

Resilience tests for the main orchestrator loop. These guard against a single
bad market or a failing opt-in feature (the health-check) taking down the
whole watch loop and, with it, every other market being monitored.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config.settings import load_settings
from src.watcher import Watcher


def make_watcher(**overrides):
    settings = load_settings()
    for key, value in overrides.items():
        object.__setattr__(settings, key, value)
    return Watcher(settings)


def fake_snapshot(market, ts=0.0):
    return SimpleNamespace(market=market, timestamp=ts, bids=[], asks=[], mid=None)


def test_tick_on_unregistered_market_does_not_raise():
    """History is keyed by the configured markets at construction time; a
    snapshot for a market outside that set must not raise KeyError.
    """
    w = make_watcher(markets=["SOL-PERP"])
    w.feed = SimpleNamespace(get_snapshot=AsyncMock(return_value=fake_snapshot("BTC-PERP")))

    asyncio.run(w._tick("BTC-PERP"))  # should not raise

    assert "BTC-PERP" in w._history
    assert len(w._history["BTC-PERP"]) == 1


def test_run_loop_survives_healthcheck_exception():
    """A failing health-check (an opt-in reliability feature) must never stop
    the poll loop or crash the process.
    """
    w = make_watcher(markets=["SOL-PERP"], run_duration_sec=0.01)
    w.feed = SimpleNamespace(get_snapshot=AsyncMock(return_value=fake_snapshot("SOL-PERP")))
    w._maybe_healthcheck = lambda: (_ for _ in ()).throw(RuntimeError("boom"))

    asyncio.run(w._run_loop())  # should return normally, not raise


def test_run_loop_survives_tick_exception():
    """A single market failing on every call (e.g. a bad feed response) must
    not stop other markets or the loop.
    """
    w = make_watcher(markets=["SOL-PERP", "BTC-PERP"], run_duration_sec=0.01)

    calls = []

    async def get_snapshot(market):
        calls.append(market)
        if market == "SOL-PERP":
            raise RuntimeError("feed exploded")
        return fake_snapshot(market)

    w.feed = SimpleNamespace(get_snapshot=get_snapshot)

    asyncio.run(w._run_loop())  # should return normally, not raise
    assert "BTC-PERP" in calls
