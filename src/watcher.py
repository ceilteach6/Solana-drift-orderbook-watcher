"""
src/watcher.py

Main orchestrator. Wires the orderbook feed, detector stack, and alert sink
together, then polls each market on a fixed interval and emits alerts.

This is the file you edit to register a new detector: add it to
``_build_detectors``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from src.alert import AlertDispatcher, build_alert_sinks
from src.collector.orderbook_feed import create_feed
from src.detector import DEFAULT_DETECTORS
from src.detector.base import Detection
from src.risk_aggregator import RiskAggregator

logger = logging.getLogger(__name__)


class Watcher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = self._build_detectors(settings)
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.risk = RiskAggregator(settings)
        self._elevated: set[str] = set()  # markets currently above composite threshold
        self.feed = None

        # Keep enough history per market to cover the flicker window.
        interval = max(settings.update_frequency_ms / 1000, 0.001)
        history_len = max(8, int(settings.flicker_window_sec / interval) + 4)
        self._history: dict[str, deque] = {
            market: deque(maxlen=history_len) for market in settings.markets
        }

    @staticmethod
    def _build_detectors(settings) -> list:
        # Register your own detector by adding it here.
        return [cls(settings) for cls in DEFAULT_DETECTORS]

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

    async def _run_loop(self) -> None:
        interval = self.settings.update_frequency_ms / 1000
        start = time.monotonic()
        duration = self.settings.run_duration_sec

        while True:
            for market in self.settings.markets:
                await self._tick(market)

            if duration and (time.monotonic() - start) >= duration:
                logger.info("Run duration reached (%.0fs) — stopping.", duration)
                return
            await asyncio.sleep(interval)

    async def _tick(self, market: str) -> None:
        try:
            snapshot = await self.feed.get_snapshot(market)
        except Exception as exc:  # one bad poll shouldn't kill the watcher
            logger.warning("Snapshot failed for %s: %s", market, exc)
            return
        if snapshot is None:
            return

        history = self._history[market]
        detections = []
        for detector in self.detectors:
            try:
                detections.extend(detector.analyze(snapshot, history))
            except Exception:
                logger.exception("Detector %s raised on %s", detector.name, market)
        history.append(snapshot)

        ema_score = self.risk.update(market, detections)
        if detections:
            self.alert.emit(detections)

        # Emit a composite alert on the rising edge of the elevated threshold only,
        # to avoid repeat-firing every tick while risk stays high.
        is_elevated_now = self.risk.is_elevated(market)
        was_elevated = market in self._elevated
        if is_elevated_now and not was_elevated:
            self.alert.emit([Detection(
                detector="risk_aggregator",
                market=market,
                score=ema_score,
                message=(
                    f"Composite risk elevated: EMA score {ema_score:.3f} ≥ "
                    f"threshold {self.settings.risk_composite_threshold} — "
                    f"sustained suspicious activity on {market}"
                ),
                details={"ema_score": round(ema_score, 4), "all_scores": self.risk.all_scores()},
            )])
        if is_elevated_now:
            self._elevated.add(market)
        else:
            self._elevated.discard(market)

    def _banner(self) -> None:
        print("🔭 Drift Orderbook Watcher — read-only")
        print(f"   Markets   : {', '.join(self.settings.markets)}")
        print(f"   Detectors : {', '.join(d.name for d in self.detectors)}")
        print(f"   Interval  : {self.settings.update_frequency_ms} ms")
        print(f"   Risk EMA  : alpha={self.settings.risk_ema_alpha}, "
              f"composite threshold={self.settings.risk_composite_threshold}")
        print(f"   Alerts    : {self.settings.alert_format} "
              f"(min score {self.settings.alert_min_score}) → "
              f"{', '.join(s.name for s in self.alert.sinks)}")
        print("   Press Ctrl+C to stop.\n")
