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
    # Consecutive snapshot failures (errors or timeouts) before the watcher
    # treats the feed as unhealthy: alerts once and forces a reconnect.
    _FEED_FAILURE_THRESHOLD = 5
    # Minimum gap between repeated "feed unhealthy" alerts, so a feed stuck
    # in a fail/reconnect/fail loop doesn't spam every tick.
    _FEED_ALERT_COOLDOWN_SEC = 60.0
    # While running on the synthetic (demo) feed, how often to retry the
    # real Drift connection in the background.
    _DEMO_RETRY_SEC = 300.0

    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = self._build_detectors(settings)
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
        self.store = SQLiteStore(settings.db_path) if settings.storage_enabled else None
        self.feed = None
        self._last_healthcheck: float = 0.0
        self._consecutive_feed_failures = 0
        self._last_feed_alert: float = float("-inf")
        self._last_demo_retry: float = 0.0
        # Guards _reconnect_feed against overlapping calls (a failure-triggered
        # reconnect and the periodic demo-mode retry could otherwise both be
        # in flight at once, each replacing self.feed and leaking the loser).
        self._reconnect_lock = asyncio.Lock()
        # The most recent off-loop storage write, so shutdown can wait for it
        # instead of racing store.close() against a write still in progress.
        self._pending_persist: asyncio.Task | None = None

        # Keep enough history per market to cover every detector's lookback
        # window (e.g. spoof_pull can need more than flicker) — derived from
        # the detectors themselves so a new windowed detector can't silently
        # under-size this buffer again.
        interval = max(settings.update_frequency_ms / 1000, 0.001)
        lookback_sec = max(
            (d.required_history_sec(settings) for d in self.detectors), default=0.0
        )
        history_len = max(8, int(lookback_sec / interval) + 4)
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
        if self.store is not None:
            self.store.connect()
        self.feed = await create_feed(self.settings)
        self._banner()  # printed after connecting so demo mode is visible
        try:
            await self._run_loop()
        finally:
            # A storage write dispatched via asyncio.to_thread() can still be
            # running on a worker thread when shutdown begins (e.g. Ctrl+C
            # mid-tick). Wait for it (shielded from the cancellation that's
            # already propagating) before closing the connection underneath
            # it, so store.close() can never run concurrently with a write.
            pending = self._pending_persist
            if pending is not None and not pending.done():
                try:
                    await asyncio.wait_for(asyncio.shield(pending), timeout=5.0)
                except Exception:
                    logger.warning("Pending storage write did not finish before shutdown")
            self.alert.close()
            await self.feed.close()
            if self.store is not None:
                self.store.close()

    async def _run_loop(self) -> None:
        interval = self.settings.update_frequency_ms / 1000
        start = time.monotonic()
        duration = self.settings.run_duration_sec
        self._last_healthcheck = start
        self._last_demo_retry = start

        while True:
            for market in self.settings.markets:
                await self._tick(market)

            self._maybe_healthcheck()
            await self._maybe_recover_from_demo()

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

    async def _tick(self, market: str) -> None:
        try:
            snapshot = await asyncio.wait_for(
                self.feed.get_snapshot(market),
                timeout=self.settings.snapshot_timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Snapshot timed out for %s (> %.1fs)",
                market, self.settings.snapshot_timeout_sec,
            )
            await self._note_feed_failure(market, "timeout")
            return
        except Exception as exc:  # one bad poll shouldn't kill the watcher
            logger.warning("Snapshot failed for %s: %s", market, exc)
            await self._note_feed_failure(market, str(exc))
            return
        self._consecutive_feed_failures = 0
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

        # SQLite commits do blocking disk I/O; keep them off the event loop
        # so a slow/contended disk can't stall orderbook polling. Tracked on
        # self._pending_persist so shutdown can wait for it instead of
        # closing the connection out from under a write in progress.
        task = asyncio.ensure_future(
            asyncio.to_thread(self._persist, market, snapshot, detections)
        )
        self._pending_persist = task
        try:
            await task
        finally:
            if self._pending_persist is task:
                self._pending_persist = None

    async def _note_feed_failure(self, market: str, reason: str) -> None:
        """Track consecutive snapshot failures and force a reconnect once
        the feed looks unhealthy, instead of logging warnings forever while
        quietly never recovering."""
        self._consecutive_feed_failures += 1
        if self._consecutive_feed_failures < self._FEED_FAILURE_THRESHOLD:
            return

        now = time.monotonic()
        if now - self._last_feed_alert >= self._FEED_ALERT_COOLDOWN_SEC:
            self._last_feed_alert = now
            logger.error(
                "Feed unhealthy: %d consecutive failures (last: %s on %s) — reconnecting",
                self._consecutive_feed_failures, reason, market,
            )
            self.alert.emit([
                Detection(
                    detector="feed_health",
                    market="-",
                    score=1.0,
                    message=(
                        f"Orderbook feed unhealthy "
                        f"({self._consecutive_feed_failures} consecutive failures) "
                        f"— attempting reconnect"
                    ),
                    details={
                        "consecutive_failures": self._consecutive_feed_failures,
                        "last_error": reason,
                    },
                )
            ])
        await self._reconnect_feed()

    async def _reconnect_feed(self) -> None:
        # Both a failure-triggered reconnect and the periodic demo-mode
        # retry call this; serialize them so two overlapping create_feed()
        # calls can never both replace self.feed, leaking whichever one loses.
        async with self._reconnect_lock:
            old_feed = self.feed
            try:
                new_feed = await create_feed(self.settings)
            except Exception:
                # self.feed is intentionally left untouched here: if it were
                # closed unconditionally (e.g. in a finally block) while the
                # reassignment above never happened, every later tick would
                # call get_snapshot() on a dead feed forever.
                logger.exception("Feed reconnect attempt failed; will retry")
                return

            self.feed = new_feed
            try:
                await old_feed.close()
            except Exception:
                logger.debug("Error closing previous feed", exc_info=True)
            self._consecutive_feed_failures = 0
            logger.info("Feed reconnected (demo=%s).", self.feed.is_demo)

    async def _maybe_recover_from_demo(self) -> None:
        """While stuck on the synthetic feed, periodically retry the real
        Drift connection in the background — the synthetic feed never
        raises, so without this the watcher would otherwise stay in demo
        mode forever once degraded, even after the outage clears."""
        if not getattr(self.feed, "is_demo", False):
            return
        now = time.monotonic()
        if now - self._last_demo_retry < self._DEMO_RETRY_SEC:
            return
        self._last_demo_retry = now
        logger.warning("Still in DEMO mode (synthetic feed) — retrying live Drift connection...")
        await self._reconnect_feed()

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
            self.store.commit()
        except Exception:
            logger.exception("Storage write failed for %s", market)

    def _banner(self) -> None:
        print("🔭 Drift Orderbook Watcher — read-only")
        if getattr(self.feed, "is_demo", False):
            print("   ⚠️  DEMO MODE — synthetic data, NOT the live Drift book.")
            print("      Retrying the real connection in the background "
                  f"every {self._DEMO_RETRY_SEC:.0f}s.")
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
