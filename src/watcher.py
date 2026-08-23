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
from src.collector.orderbook_feed import DriftOrderbookFeed, create_feed
from src.detector import DEFAULT_DETECTORS
from src.detector.base import Detection
from src.risk import RiskAggregator
from src.selftest import run_selftest
from src.storage import SQLiteStore

logger = logging.getLogger(__name__)


def history_length(settings) -> int:
    """How many snapshots to retain per market.

    Must cover the longest lookback window any detector scans via
    ``history`` (currently flicker and spoof-pull) at the configured poll
    interval, plus a small safety margin — otherwise the deque silently
    evicts snapshots a detector still expects to see.
    """
    interval = max(settings.update_frequency_ms / 1000, 0.001)
    widest_window = max(settings.flicker_window_sec, settings.spoof_window_sec)
    return max(8, int(widest_window / interval) + 4)


class Watcher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = self._build_detectors(settings)
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
        self.store = SQLiteStore(settings.db_path) if settings.storage_enabled else None
        self.feed = None
        self._last_healthcheck: float = 0.0
        # Feed liveness: a snapshot exception/None is caught and logged per
        # tick (so one bad poll can't kill the watcher), but that same
        # isolation means an RPC that never recovers would otherwise poll
        # forever without anyone ever finding out. These two counters turn
        # "keeps failing" into an actual reconnect, and "hasn't succeeded in
        # a while" into an alertable health-check signal.
        self._feed_consecutive_failures: int = 0
        self._last_snapshot_ok: float = 0.0

        history_len = history_length(settings)
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
        self._last_snapshot_ok = start

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

        # run_selftest() exercises detector *logic* against hand-built
        # scenarios, deliberately independent of the live feed — so it
        # reports "OK" just as happily whether the real Drift connection is
        # healthy or has been dead for hours. Check actual feed liveness as
        # its own signal instead of folding it into (or omitting it from)
        # the self-test.
        try:
            stale_for = now - self._last_snapshot_ok
            if stale_for > self.settings.feed_stale_after_sec:
                logger.warning(
                    "Health-check FAILED: no successful orderbook snapshot in %.0fs",
                    stale_for,
                )
                self.alert.emit([
                    Detection(
                        detector="healthcheck",
                        market="-",
                        score=1.0,
                        message=f"Live feed stale: no successful snapshot in {stale_for:.0f}s",
                        details={"stale_for_sec": stale_for},
                    )
                ])
        except Exception:
            logger.exception("Health-check feed-liveness check failed")

        try:
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
        except Exception:
            # A broken self-test or alert sink must not take down the poll
            # loop for every market — log and let the next cycle retry.
            logger.exception("Health-check cycle failed")

    async def _tick(self, market: str) -> None:
        try:
            snapshot = await self.feed.get_snapshot(market)
        except Exception as exc:  # one bad poll shouldn't kill the watcher
            logger.warning("Snapshot failed for %s: %s", market, exc)
            await self._note_feed_failure()
            return
        if snapshot is None:
            await self._note_feed_failure()
            return

        self._feed_consecutive_failures = 0
        self._last_snapshot_ok = time.monotonic()

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
            # A malformed Detection or a broken alert sink must not kill the
            # whole watcher — persistence below should still run.
            logger.exception("Risk aggregation / alert emission failed for %s", market)

        await self._persist(market, snapshot, detections)

    async def _note_feed_failure(self) -> None:
        self._feed_consecutive_failures += 1
        if self._feed_consecutive_failures < self.settings.feed_max_consecutive_failures:
            return
        # Reset immediately, win or lose: a reconnect attempt itself may keep
        # failing (RPC still down), and without this the counter would stay
        # pinned at/above the threshold and re-trigger a reconnect on every
        # single tick instead of waiting for another full failure streak.
        self._feed_consecutive_failures = 0
        await self._reconnect_feed()

    async def _reconnect_feed(self) -> None:
        # The synthetic feed is a local generator, not a network connection —
        # it has nothing to reconnect and its "failures" (if any) would be a
        # bug, not an outage a retry could fix.
        if not isinstance(self.feed, DriftOrderbookFeed):
            return
        logger.warning(
            "Feed unhealthy (%d consecutive snapshot failures) — reconnecting.",
            self.settings.feed_max_consecutive_failures,
        )
        old_feed = self.feed
        try:
            new_feed = DriftOrderbookFeed(self.settings)
            await new_feed.connect()
        except Exception:
            # Deliberately do NOT fall back to create_feed()'s synthetic
            # feed here: that fallback exists so the tool still runs when
            # driftpy is unavailable at *startup*. Silently switching a
            # previously-live connection to fake data mid-run would make the
            # watcher look healthy while alerting on data nobody generated —
            # worse than the outage it would be masking. Keep polling the
            # old (broken) feed and let the next failure streak retry.
            logger.exception("Feed reconnect failed; will retry on the next failure streak")
            self.alert.emit([
                Detection(
                    detector="healthcheck",
                    market="-",
                    score=1.0,
                    message="Feed reconnect failed — live data may be unavailable",
                    details={},
                )
            ])
            return
        self.feed = new_feed
        self._last_snapshot_ok = time.monotonic()
        logger.info("Feed reconnected.")
        try:
            await old_feed.close()
        except Exception:
            logger.exception("Error closing previous feed connection after reconnect")

    async def _persist(self, market: str, snapshot, detections) -> None:
        if self.store is None:
            return
        try:
            risk = self.aggregator.score(market) if self.aggregator is not None else None
            # record_tick() commits (an fsync) synchronously; run it off the
            # event loop so a slow disk/lock doesn't stall every other
            # market's polling, health-checks, and alert delivery for the
            # duration of the write.
            await asyncio.to_thread(
                self.store.record_tick,
                market, snapshot, detections, risk,
                persist_snapshot=self.settings.persist_snapshots,
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
