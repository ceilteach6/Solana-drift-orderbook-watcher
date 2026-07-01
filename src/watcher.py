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

# If every registered detector raises on this many consecutive ticks for a
# market, something is structurally broken (a bad settings combination that
# only manifests on live data shapes, a driftpy/library version mismatch,
# etc.) rather than a one-off bad snapshot. Distinct from HEALTHCHECK_ENABLED
# (which probes with synthetic known-positive scenarios on a timer): this
# fires from what's actually happening on live ticks, so it still catches the
# failure even when the operator hasn't opted into the periodic self-test.
_DETECTOR_STACK_FAILURE_ALERT_THRESHOLD = 5


class Watcher:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.detectors = self._build_detectors(settings)
        self.alert = AlertDispatcher(settings, build_alert_sinks(settings))
        self.aggregator = RiskAggregator(settings) if settings.risk_aggregation else None
        self.store = SQLiteStore(settings.db_path) if settings.storage_enabled else None
        self.feed = None
        self._last_healthcheck: float = 0.0
        self._consecutive_detector_failures: dict[str, int] = {}
        self._detector_stack_alerting: dict[str, bool] = {}

        # Keep enough history per market to cover the flicker window.
        # (Settings.__post_init__ guarantees update_frequency_ms > 0.)
        self._interval = settings.update_frequency_ms / 1000
        self._history_len = max(8, int(settings.flicker_window_sec / self._interval) + 4)
        self._history: dict[str, deque] = {
            market: deque(maxlen=self._history_len) for market in settings.markets
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
            # Writes run via asyncio.to_thread (see _tick), which doesn't
            # guarantee the same worker thread across calls; access here is
            # strictly sequential (always awaited before the next write), so
            # this is safe without an additional lock.
            self.store.connect(check_same_thread=False)
        self.feed = await create_feed(self.settings)
        try:
            await self._run_loop()
        finally:
            await self.feed.close()
            if self.store is not None:
                self.store.close()

    async def _run_loop(self) -> None:
        interval = self._interval
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
            return
        if snapshot is None:
            return

        # setdefault rather than a plain lookup: robust to a market not present
        # in the dict built at construction time (e.g. Settings assembled by a
        # caller other than load_settings()).
        history = self._history.setdefault(market, deque(maxlen=self._history_len))
        detections = []
        failures = 0
        for detector in self.detectors:
            try:
                detections.extend(detector.analyze(snapshot, history))
            except Exception:
                failures += 1
                logger.exception("Detector %s raised on %s", detector.name, market)
        history.append(snapshot)
        self._track_detector_stack_health(market, failures)

        if self.aggregator is not None:
            # Consolidate into a single smoothed risk signal per market.
            risk = self.aggregator.update(market, snapshot.timestamp, detections)
            if risk is not None:
                self.alert.emit([risk])
        elif detections:
            # Raw mode: one alert per detection.
            self.alert.emit(detections)

        if self.store is not None:
            # sqlite3 calls block; run off the event loop so a slow disk/fsync
            # doesn't stall other markets' ticks or the health-check timer.
            await asyncio.to_thread(self._persist, market, snapshot, detections)

    def _track_detector_stack_health(self, market: str, failures: int) -> None:
        """Alert (once) when every detector has raised for several ticks in a row.

        A single bad tick isn't news — each detector already gets its own
        try/except in ``_tick`` and other markets/detectors keep running. But
        if *all* detectors keep raising for this market, the risk aggregator
        keeps computing over an empty detection list and silently reports
        "calm" — indistinguishable in the logs (short of grepping for
        tracebacks) from a genuinely quiet market. This turns that into a
        loud, one-time alert via the normal alert sinks.
        """
        total = len(self.detectors)
        if total == 0 or failures < total:
            self._consecutive_detector_failures[market] = 0
            self._detector_stack_alerting[market] = False
            return

        count = self._consecutive_detector_failures.get(market, 0) + 1
        self._consecutive_detector_failures[market] = count
        if count < _DETECTOR_STACK_FAILURE_ALERT_THRESHOLD:
            return
        if self._detector_stack_alerting.get(market, False):
            return  # already alerted for this outage; stay quiet until it clears

        self._detector_stack_alerting[market] = True
        logger.error(
            "All %d detector(s) failed for %s on %d consecutive ticks",
            total, market, count,
        )
        self.alert.emit([
            Detection(
                detector="detector_stack_failure",
                market=market,
                score=1.0,
                message=(
                    f"All {total} detectors raised for {count} consecutive "
                    f"ticks on {market} — detector stack is broken, not just "
                    f"a quiet market"
                ),
                details={"consecutive_failures": count},
            )
        ])

    def _persist(self, market: str, snapshot, detections) -> None:
        """Blocking; call via ``asyncio.to_thread`` (only invoked when store is set)."""
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
