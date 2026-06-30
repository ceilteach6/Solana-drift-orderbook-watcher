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
        self._last_prune: float = 0.0
        self._feed_failure_streak: int = 0
        self._last_reconnect_attempt: float = float("-inf")
        self._storage_failure_streak: int = 0

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
        self._last_prune = start

        while True:
            for market in self.settings.markets:
                await self._tick(market)

            self._maybe_healthcheck()
            self._maybe_prune_storage()

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

    def _maybe_prune_storage(self) -> None:
        if self.store is None or self.settings.storage_retention_sec <= 0:
            return
        now = time.monotonic()
        if now - self._last_prune < self.settings.storage_prune_interval_sec:
            return
        self._last_prune = now
        try:
            deleted = self.store.prune(self.settings.storage_retention_sec, time.time())
        except Exception:
            logger.exception("Storage prune failed")
            return
        total = sum(deleted.values())
        if total:
            logger.info(
                "Pruned %d old storage rows (retention %.0fs): %s",
                total, self.settings.storage_retention_sec, deleted,
            )

    async def _maybe_reconnect_feed(self) -> None:
        """Rebuild the feed after sustained failures.

        A dropped websocket on the real Drift feed doesn't surface as a clean
        exception every time — ``get_l2_orderbook_sync`` can just keep
        returning stale/None data. Tracking a consecutive-failure streak and
        rebuilding via ``create_feed`` (which itself falls back to synthetic
        if the real feed can't be re-established) is the only way this
        watcher recovers without manual intervention.
        """
        if self._feed_failure_streak < self.settings.feed_reconnect_after_failures:
            return
        now = time.monotonic()
        if now - self._last_reconnect_attempt < self.settings.feed_reconnect_cooldown_sec:
            return
        self._last_reconnect_attempt = now
        logger.warning(
            "Feed failed %d consecutive ticks — reconnecting.",
            self._feed_failure_streak,
        )
        old_feed = self.feed
        try:
            self.feed = await create_feed(self.settings)
        except Exception:
            logger.exception("Feed reconnect attempt failed; will retry.")
            return
        if old_feed is not None:
            try:
                await old_feed.close()
            except Exception:
                logger.debug("Error closing stale feed during reconnect", exc_info=True)
        self._feed_failure_streak = 0
        logger.info("Feed reconnected.")

    async def _tick(self, market: str) -> None:
        try:
            snapshot = await self.feed.get_snapshot(market)
        except Exception as exc:  # one bad poll shouldn't kill the watcher
            logger.warning("Snapshot failed for %s: %s", market, exc)
            self._feed_failure_streak += 1
            await self._maybe_reconnect_feed()
            return
        self._feed_failure_streak = 0
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
            try:
                risk = self.aggregator.update(market, snapshot.timestamp, detections)
            except Exception:
                logger.exception("Risk aggregator raised on %s", market)
                risk = None
            if risk is not None:
                self.alert.emit([risk])
        elif detections:
            # Raw mode: one alert per detection.
            self.alert.emit(detections)

        self._persist(market, snapshot, detections)

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
            self._storage_failure_streak = 0
        except Exception:
            self._storage_failure_streak += 1
            logger.exception(
                "Storage write failed for %s (%d consecutive failures)",
                market, self._storage_failure_streak,
            )
            self._maybe_disable_storage()

    def _maybe_disable_storage(self) -> None:
        """Stop hammering a permanently broken store (e.g. disk full).

        Without this, a persistent failure logs (and silently drops data)
        forever, once per market per tick, with no operator-visible signal
        beyond grepping logs.
        """
        if self._storage_failure_streak < self.settings.storage_failure_circuit_breaker:
            return
        logger.error(
            "Disabling storage after %d consecutive write failures.",
            self._storage_failure_streak,
        )
        self.alert.emit([
            Detection(
                detector="storage",
                market="-",
                score=1.0,
                message=(
                    f"Storage disabled after {self._storage_failure_streak} "
                    "consecutive write failures — check disk space / DB health."
                ),
                details={"consecutive_failures": self._storage_failure_streak},
            )
        ])
        try:
            self.store.close()
        except Exception:
            logger.debug("Error closing failed store", exc_info=True)
        self.store = None

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
