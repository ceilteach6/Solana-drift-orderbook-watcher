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
from src.metrics import MetricsRegistry, start_metrics_server
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
        self.metrics = MetricsRegistry()
        self._metrics_server = None
        self.feed = None
        self._last_healthcheck: float = 0.0

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
        if self.settings.metrics_enabled:
            self._metrics_server = start_metrics_server(
                self.metrics, self.settings.metrics_host, self.settings.metrics_port
            )
        self.feed = await create_feed(self.settings)
        try:
            await self._run_loop()
        finally:
            await self.feed.close()
            if self.store is not None:
                self.store.close()
            if self._metrics_server is not None:
                self._metrics_server.shutdown()
                self._metrics_server.server_close()

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
            self.metrics.inc("watcher_snapshot_errors_total", {"market": market})
            return
        if snapshot is None:
            return
        self.metrics.inc("watcher_ticks_total", {"market": market})

        history = self._history[market]
        detections = []
        for detector in self.detectors:
            try:
                detections.extend(detector.analyze(snapshot, history))
            except Exception:
                logger.exception("Detector %s raised on %s", detector.name, market)
        history.append(snapshot)
        for d in detections:
            self.metrics.inc(
                "watcher_detections_total", {"market": market, "detector": d.detector}
            )

        try:
            if self.aggregator is not None:
                # Consolidate into a single smoothed risk signal per market.
                risk = self.aggregator.update(market, snapshot.timestamp, detections)
                self.metrics.set(
                    "watcher_risk_score", {"market": market}, self.aggregator.score(market)
                )
                if risk is not None:
                    emitted = self.alert.emit([risk])
                    self.metrics.inc("watcher_alerts_emitted_total", {"market": market}, emitted)
            elif detections:
                # Raw mode: one alert per detection.
                emitted = self.alert.emit(detections)
                self.metrics.inc("watcher_alerts_emitted_total", {"market": market}, emitted)
        except Exception:
            # A malformed Detection or a broken alert sink must not kill the
            # whole watcher — persistence below should still run.
            logger.exception("Risk aggregation / alert emission failed for %s", market)

        await self._persist(market, snapshot, detections)

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
            self.metrics.inc("watcher_storage_errors_total", {"market": market})

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
        if self.settings.metrics_enabled:
            print(f"   Metrics   : http://{self.settings.metrics_host}:"
                  f"{self.settings.metrics_port}/metrics")
        print("   Press Ctrl+C to stop.\n")
