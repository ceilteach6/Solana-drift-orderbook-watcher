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
from src.risk import RiskAggregator
from src.selftest import run_selftest
from src.storage import SQLiteStore

logger = logging.getLogger(__name__)


class Watcher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = self._build_detectors(settings)
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
        self.store = SQLiteStore(settings.db_path) if settings.storage_enabled else None
        self.feed = None
        self._last_healthcheck: float = 0.0
        self._consecutive_failures: int = 0

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
        if self.store is not None:
            self.store.connect()
        self.feed = await create_feed(self.settings)
        try:
            await self._run_loop()
        finally:
            await self.feed.close()
            if self.store is not None:
                self.store.close()

    async def _run_loop(self) -> None:
        interval = self.settings.update_frequency_ms / 1000
        start = time.monotonic()
        duration = self.settings.run_duration_sec
        self._last_healthcheck = start

        while True:
            for market in self.settings.markets:
                await self._tick(market)

            self._maybe_healthcheck()

            if duration and (time.monotonic() - start) >= duration:
                logger.info("Run duration reached (%.0fs) — stopping.", duration)
                return
            await asyncio.sleep(interval)

    def _maybe_healthcheck(self) -> None:
        if not self.settings.healthcheck_enabled:
            return
        now = time.monotonic()
        if now - self._last_healthcheck < self.settings.healthcheck_interval_sec:
            return
        self._last_healthcheck = now

        try:
            results = run_selftest(self.settings)
        except Exception:  # a broken self-test must not take the watcher down
            logger.exception("Health-check self-test raised")
            return
        failed = [r.name for r in results if not r.passed]
        if failed:
            logger.warning("Health-check FAILED: %s not firing", ", ".join(failed))
            self.alert.emit([
                Detection(
                    detector="healthcheck",
                    market="-",
                    score=1.0,
                    message=f"Self-test failed: {', '.join(failed)} not firing",
                    details={"failed": failed},
                )
            ])
        else:
            logger.debug("Health-check OK (%d checks passed)", len(results))

    async def _tick(self, market: str) -> None:
        try:
            snapshot = await asyncio.wait_for(
                self.feed.get_snapshot(market), timeout=self.settings.snapshot_timeout_sec
            )
        except Exception as exc:  # one bad/hung poll shouldn't kill the watcher
            logger.warning("Snapshot failed for %s: %s", market, exc)
            await self._on_snapshot_failure()
            return
        self._consecutive_failures = 0
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

        if self.aggregator is not None:
            # Consolidate into a single smoothed risk signal per market.
            risk = self.aggregator.update(market, snapshot.timestamp, detections)
            if risk is not None:
                self.alert.emit([risk])
        elif detections:
            # Raw mode: one alert per detection.
            self.alert.emit(detections)

        self._persist(market, snapshot, detections)

    async def _on_snapshot_failure(self) -> None:
        """Track repeated feed failures and reconnect once they pile up.

        A single bad poll is normal (transient RPC hiccup) and is just logged
        by the caller. But the feed never recovers on its own after a
        websocket/RPC drop, so without this the watcher quietly degrades to
        zero coverage forever while still looking "alive". Reconnecting after
        a run of failures gives it a chance to self-heal.
        """
        self._consecutive_failures += 1
        threshold = self.settings.feed_reconnect_after_failures
        if threshold <= 0 or self._consecutive_failures < threshold:
            return

        logger.warning(
            "%d consecutive snapshot failures — reconnecting feed.",
            self._consecutive_failures,
        )
        self._consecutive_failures = 0  # always reset: don't reconnect every tick
        try:
            await self.feed.close()
        except Exception:
            logger.exception("Error closing feed before reconnect")
        try:
            self.feed = await create_feed(self.settings)
            logger.info("Feed reconnected.")
        except Exception:
            logger.exception("Feed reconnect failed; will retry after %d more failures", threshold)

    def _persist(self, market: str, snapshot, detections) -> None:
        if self.store is None:
            return
        try:
            if self.settings.persist_snapshots:
                self.store.record_snapshot(snapshot)
            self.store.record_detections(snapshot.timestamp, detections)
            if self.aggregator is not None:
                self.store.record_risk(
                    market, snapshot.timestamp,
                    self.aggregator.score(market), snapshot.mid,
                )
        except Exception:
            logger.exception("Storage write failed for %s", market)

    def _banner(self) -> None:
        print("🔭 Drift Orderbook Watcher — read-only")
        print(f"   Markets   : {', '.join(self.settings.markets)}")
        print(f"   Detectors : {', '.join(d.name for d in self.detectors)}")
        mode = "risk-aggregated" if self.aggregator else "raw per-detection"
        print(f"   Mode      : {mode}")
        print(f"   Interval  : {self.settings.update_frequency_ms} ms")
        print(f"   Alerts    : {self.settings.alert_format} "
              f"(min score {self.settings.alert_min_score}) → "
              f"{', '.join(s.name for s in self.alert.sinks)}")
        if self.settings.healthcheck_enabled:
            print(f"   Health    : self-test every "
                  f"{self.settings.healthcheck_interval_sec:.0f}s")
        if self.store is not None:
            snaps = " + snapshots" if self.settings.persist_snapshots else ""
            print(f"   Storage   : {self.settings.db_path} "
                  f"(detections + risk{snaps})")
        print("   Press Ctrl+C to stop.\n")
