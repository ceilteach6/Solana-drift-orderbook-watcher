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

# Reconnect the feed after this many consecutive missed/failed ticks for any
# one market (rather than waiting for an exception that a silently-stale
# websocket subscription may never raise).
_RECONNECT_FAILURE_THRESHOLD = 5
_RECONNECT_BACKOFF_BASE_SEC = 5.0
_RECONNECT_BACKOFF_MAX_SEC = 300.0


class Watcher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = self._build_detectors(settings)
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
        self.store = SQLiteStore(settings.db_path) if settings.storage_enabled else None
        self.feed = None
        self._last_healthcheck: float = 0.0

        # Computed once and reused everywhere (run loop cadence + history
        # sizing) so the two can't drift out of sync with each other.
        self._interval = max(settings.update_frequency_ms / 1000, 0.001)

        # Keep enough history per market to cover the longest lookback window
        # any detector uses (currently flicker and spoof-pull), not just
        # flicker's — otherwise a detector with a longer window silently sees
        # a truncated history.
        lookback_sec = max(settings.flicker_window_sec, settings.spoof_window_sec)
        history_len = max(8, int(lookback_sec / self._interval) + 4)
        self._history: dict[str, deque] = {
            market: deque(maxlen=history_len) for market in settings.markets
        }

        # Reconnect bookkeeping.
        self._consecutive_failures: dict[str, int] = {}
        self._reconnect_backoff = _RECONNECT_BACKOFF_BASE_SEC
        self._reconnect_not_before = 0.0

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
            try:
                self.store.connect()
            except Exception:
                # A broken DB path/permissions shouldn't take down the whole
                # watcher — disable persistence and keep watching.
                logger.exception(
                    "Storage init failed (%s) — continuing without persistence.",
                    self.settings.db_path,
                )
                self.store = None
        self.feed = await create_feed(self.settings)
        try:
            await self._run_loop()
        finally:
            await self.feed.close()
            if self.store is not None:
                self.store.close()

    async def _run_loop(self) -> None:
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
            await asyncio.sleep(self._interval)

    def _maybe_healthcheck(self) -> None:
        if not self.settings.healthcheck_enabled:
            return
        now = time.monotonic()
        if now - self._last_healthcheck < self.settings.healthcheck_interval_sec:
            return
        self._last_healthcheck = now

        results = run_selftest(self.settings)
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
            snapshot = await self.feed.get_snapshot(market)
        except Exception as exc:  # one bad poll shouldn't kill the watcher
            logger.warning("Snapshot failed for %s: %s", market, exc)
            snapshot = None
            self._consecutive_failures[market] = self._consecutive_failures.get(market, 0) + 1
        else:
            # A None snapshot (e.g. a silently-stale subscription) is also a
            # miss for reconnect purposes, even though nothing raised.
            if snapshot is None:
                self._consecutive_failures[market] = self._consecutive_failures.get(market, 0) + 1
            else:
                self._consecutive_failures[market] = 0
                self._reconnect_backoff = _RECONNECT_BACKOFF_BASE_SEC

        if max(self._consecutive_failures.values(), default=0) >= _RECONNECT_FAILURE_THRESHOLD:
            await self._maybe_reconnect_feed()

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

        try:
            if self.aggregator is not None:
                # Consolidate into a single smoothed risk signal per market.
                risk = self.aggregator.update(market, snapshot.timestamp, detections)
                if risk is not None:
                    self.alert.emit([risk])
            elif detections:
                # Raw mode: one alert per detection.
                self.alert.emit(detections)
        except Exception:
            # Same "one bad subsystem shouldn't kill the watcher" rule as the
            # detector loop and storage below — a bad score from a future
            # detector or a sink misbehaving shouldn't kill every market.
            logger.exception("Risk/alert dispatch raised for %s", market)

        self._persist(market, snapshot, detections)

    async def _maybe_reconnect_feed(self) -> None:
        now = time.monotonic()
        if now < self._reconnect_not_before:
            return

        logger.warning(
            "Repeated snapshot misses across markets — reconnecting feed."
        )
        try:
            await self.feed.close()
        except Exception:
            logger.debug("Error closing stale feed", exc_info=True)

        try:
            self.feed = await create_feed(self.settings)
        except Exception:
            logger.exception("Feed reconnect failed")
        finally:
            # Backoff regardless of outcome: create_feed() falls back to the
            # synthetic feed internally rather than raising, so a failed
            # *real* reconnect wouldn't otherwise be visible here. Backing
            # off avoids hot-looping reconnect attempts against a down RPC.
            self._reconnect_not_before = now + self._reconnect_backoff
            self._reconnect_backoff = min(
                self._reconnect_backoff * 2, _RECONNECT_BACKOFF_MAX_SEC
            )
            self._consecutive_failures.clear()

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
