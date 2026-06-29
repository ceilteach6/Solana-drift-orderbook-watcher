"""
src/watcher.py

Main orchestrator.  Wires the orderbook feed, detector stack, and alert
dispatcher together, then polls each market on a fixed interval and emits
alerts.

To register a new detector: add its class to DEFAULT_DETECTORS in
``src/detector/__init__.py``.  That's it — nothing else needs to change.

Per-market circuit-breaker
--------------------------
If a market's snapshot call fails repeatedly, the watcher backs off
exponentially (skipping 1, 2, 4, 8, 16, 32 cycles) before trying again.
This prevents a broken RPC from flooding the log and burning CPU.  A single
successful snapshot resets the counter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from src.alert import AlertDispatcher, build_alert_sinks
from src.collector.orderbook_feed import create_feed
from src.detector import DEFAULT_DETECTORS

logger = logging.getLogger(__name__)

# Cycle counts to skip after 1, 2, 3, … consecutive failures on a market.
_BACKOFF_CYCLES = (1, 2, 4, 8, 16, 32)


class Watcher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = [cls(settings) for cls in DEFAULT_DETECTORS]
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.feed = None

        interval = max(settings.update_frequency_ms / 1000, 0.001)
        history_len = max(8, int(settings.flicker_window_sec / interval) + 4)
        self._history: dict[str, deque] = {
            market: deque(maxlen=history_len) for market in settings.markets
        }

        # Circuit-breaker state per market.
        self._failures: dict[str, int] = {m: 0 for m in settings.markets}
        self._skip_remaining: dict[str, int] = {m: 0 for m in settings.markets}

    async def start(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        self._banner()
        self.feed = await create_feed(self.settings)
        try:
            await self._run_loop()
        finally:
            await self.feed.close()
            self.alert.close()

    async def _run_loop(self) -> None:
        interval = self.settings.update_frequency_ms / 1000
        start = time.monotonic()
        duration = self.settings.run_duration_sec

        while True:
            t0 = time.monotonic()
            for market in self.settings.markets:
                await self._tick(market)

            if duration and (time.monotonic() - start) >= duration:
                logger.info("Run duration reached (%.0fs) — stopping.", duration)
                return
            # Compensate for processing time so the actual poll rate stays stable.
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))

    async def _tick(self, market: str) -> None:
        # Circuit-breaker: skip this cycle if we're in backoff.
        if self._skip_remaining[market] > 0:
            self._skip_remaining[market] -= 1
            return

        try:
            snapshot = await self.feed.get_snapshot(market)
            self._failures[market] = 0  # reset on success
        except Exception as exc:
            n = self._failures[market] = self._failures[market] + 1
            skip = _BACKOFF_CYCLES[min(n - 1, len(_BACKOFF_CYCLES) - 1)]
            self._skip_remaining[market] = skip
            logger.warning(
                "Snapshot failed for %s (%d× consecutive): %s — pausing %d cycle(s)",
                market, n, exc, skip,
            )
            return

        if snapshot is None:
            return

        history = self._history[market]
        detections: list = []
        for detector in self.detectors:
            try:
                detections.extend(detector.analyze(snapshot, history))
            except Exception:
                logger.exception("Detector %r raised on %s", detector.name, market)
        history.append(snapshot)

        if detections:
            await self.alert.emit(detections)

    def _banner(self) -> None:
        sink_names = ", ".join(s.name for s in self.alert.sinks)
        print("🔭 Drift Orderbook Watcher — read-only")
        print(f"   Markets   : {', '.join(self.settings.markets)}")
        print(f"   Detectors : {', '.join(d.name for d in self.detectors)}")
        print(f"   Interval  : {self.settings.update_frequency_ms} ms")
        print(
            f"   Alerts    : {self.settings.alert_format} "
            f"(min score {self.settings.alert_min_score}) → {sink_names}"
        )
        print("   Press Ctrl+C to stop.\n")
