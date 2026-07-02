"""
src/collector/orderbook_feed.py

Normalized L2 orderbook model + feed implementations.

Two feeds are provided:

* :class:`DriftOrderbookFeed` — the real feed, backed by the ``driftpy`` DLOB
  subscriber (see :mod:`src.collector.drift_client`).
* :class:`SyntheticOrderbookFeed` — a self-contained, network-free generator
  used as a fallback (and for examples/tests). It produces plausible orderbooks
  and occasionally injects bot-like patterns so the detectors have something to
  react to.

``create_feed()`` tries the real feed first and transparently falls back to the
synthetic one if ``driftpy`` is unavailable or the connection fails, so
``python main.py`` always runs.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    # driftpy's own precision constants, so we stay correct if a future
    # release changes them.
    from driftpy.constants.numeric_constants import BASE_PRECISION, PRICE_PRECISION
except Exception:  # driftpy optional / different version — fall back to the
    # documented Drift fixed-point precisions (price: 1e6, base amount: 1e9).
    PRICE_PRECISION = 10**6
    BASE_PRECISION = 10**9


# --------------------------------------------------------------------------- #
# Normalized data model (what the detectors consume)
# --------------------------------------------------------------------------- #
@dataclass
class Level:
    """A single price level in the L2 orderbook."""

    price: float
    size: float


@dataclass
class OrderbookSnapshot:
    """An aggregated L2 orderbook snapshot for one market at one instant."""

    market: str
    timestamp: float  # epoch seconds
    bids: list[Level] = field(default_factory=list)  # descending price
    asks: list[Level] = field(default_factory=list)  # ascending price

    @property
    def mid(self) -> float | None:
        if self.bids and self.asks:
            return (self.bids[0].price + self.asks[0].price) / 2
        return None


# --------------------------------------------------------------------------- #
# Feed interface
# --------------------------------------------------------------------------- #
class OrderbookFeed:
    """Abstract async feed: connect, pull snapshots, close."""

    async def connect(self) -> None:  # pragma: no cover - trivial
        return None

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover - trivial
        return None


# --------------------------------------------------------------------------- #
# Real feed (driftpy DLOB)
# --------------------------------------------------------------------------- #
class DriftOrderbookFeed(OrderbookFeed):
    """Reads L2 orderbooks from the live Drift DLOB via ``driftpy``."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._stack = None  # populated in connect()
        # A dedicated pool, not the process-wide default executor: wait_for()
        # only cancels the *await* on timeout, the worker thread keeps running
        # get_l2_orderbook_sync to completion (Python threads can't be force-
        # killed). Against a hung RPC, every poll tick pins another thread
        # that never frees up; on the shared default executor that eventually
        # starves *any other code in the process* using run_in_executor(None,
        # ...). Confined to its own bounded pool, a stuck RPC can only
        # exhaust the feed's own threads.
        self._executor = ThreadPoolExecutor(
            max_workers=max(4, len(settings.markets)), thread_name_prefix="drift-l2"
        )

    async def connect(self) -> None:
        # Imported lazily so the package works without driftpy installed.
        from src.collector.drift_client import DriftStack

        self._stack = await DriftStack.build(self.settings)
        logger.info("Connected to Drift (%s)", self.settings.drift_env)

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        if self._stack is None:
            raise RuntimeError("connect() must be called before get_snapshot()")
        # get_l2() is a blocking, synchronous call into driftpy's DLOB
        # internals. Run it off the event loop with a hard timeout so a
        # slow/stuck RPC/DLOB call can't freeze every market's polling,
        # health-checks, and alert delivery indefinitely.
        loop = asyncio.get_running_loop()
        raw = await asyncio.wait_for(
            loop.run_in_executor(
                self._executor, self._stack.get_l2, market, self.settings.orderbook_depth
            ),
            timeout=self.settings.snapshot_timeout_sec,
        )
        if raw is None:
            return None
        return _snapshot_from_driftpy(market, raw)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.close()
        # shutdown(wait=False) only stops *this* method from blocking. It does
        # NOT stop CPython's own atexit hook (concurrent.futures.thread's
        # _python_exit) from unconditionally joining every worker thread ever
        # created by any ThreadPoolExecutor in the process — including one
        # wedged inside a blocking get_l2_orderbook_sync call that ignored
        # snapshot_timeout_sec (that timeout only cancels the await, not the
        # thread; Python threads can't be force-killed). Without the watchdog
        # below, a single stuck poll hangs the whole process at interpreter
        # exit, silently defeating systemd/Docker/k8s restart-on-exit.
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._arm_shutdown_watchdog()

    def _arm_shutdown_watchdog(self) -> None:
        """Guarantee the process actually terminates even if a worker thread
        is unkillably wedged in a blocking RPC call.

        Arms only at interpreter-exit time (via ``atexit``), not immediately —
        this must not fire while the process is otherwise healthy and simply
        continuing to run (e.g. the dashboard, or a long test session).
        ``atexit`` callbacks run LIFO, and ``concurrent.futures.thread``
        registers its own thread-joining hook at import time, before this
        method ever runs — so registering here guarantees our watchdog starts
        counting down *before* that hook can block on ``Thread.join()``.
        """
        grace = max(getattr(self.settings, "snapshot_timeout_sec", 5.0), 1.0) + 5.0

        def _start_timer() -> None:
            timer = threading.Timer(grace, os._exit, args=(1,))
            timer.daemon = True
            timer.start()

        atexit.register(_start_timer)


def _to_raw(value) -> float:
    """Coerce a driftpy numeric field (int, BN-like, or already-float) to a
    plain float, without applying any precision scaling."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(getattr(value, "value", 0))


def _snapshot_from_driftpy(market: str, l2) -> OrderbookSnapshot:
    # driftpy's L2 levels carry raw on-chain fixed-point integers, not
    # human-readable numbers: price in units of 1/PRICE_PRECISION, size in
    # units of 1/BASE_PRECISION. Skipping this division (as an earlier version
    # of this function did) is only masked in tests/--selftest, which never
    # exercise the real driftpy path — against a live RPC every price/size
    # would silently be off by ~1e6x/1e9x.
    def levels(side) -> list[Level]:
        out: list[Level] = []
        for lvl in side or []:
            price = getattr(lvl, "price", None)
            size = getattr(lvl, "size", None)
            if price is None and isinstance(lvl, dict):
                price, size = lvl.get("price"), lvl.get("size")
            out.append(Level(
                _to_raw(price) / PRICE_PRECISION,
                _to_raw(size) / BASE_PRECISION,
            ))
        return out

    bids = levels(getattr(l2, "bids", None) or [])
    asks = levels(getattr(l2, "asks", None) or [])
    # Every downstream consumer (mid price, best_bid/ask, spread, the
    # imbalance detector's top-N slice) assumes bids are descending and asks
    # ascending. driftpy's DLOB conventionally returns levels in that order,
    # but nothing in this module verified it — sort defensively so a future
    # driftpy release (or an unconventional DLOB response) can't silently
    # corrupt every price-derived signal.
    bids.sort(key=lambda lvl: lvl.price, reverse=True)
    asks.sort(key=lambda lvl: lvl.price)
    return OrderbookSnapshot(market=market, timestamp=time.time(), bids=bids, asks=asks)


# --------------------------------------------------------------------------- #
# Synthetic feed (network-free fallback / demo)
# --------------------------------------------------------------------------- #
class SyntheticOrderbookFeed(OrderbookFeed):
    """Generates plausible orderbooks on a fixed price grid.

    A fixed grid means price levels recur across snapshots, which lets the
    flicker detector observe levels appearing/disappearing. With small
    probability it injects a repeated-size "wall" so the repeated-size and
    layering detectors fire too.
    """

    _SEED_MIDS = {"SOL-PERP": 150.0, "BTC-PERP": 65000.0, "ETH-PERP": 3400.0}

    def __init__(self, settings, *, rng: random.Random | None = None) -> None:
        self.settings = settings
        self._rng = rng or random.Random()
        self._mids: dict[str, float] = {}

    async def connect(self) -> None:
        for market in self.settings.markets:
            self._mids[market] = self._SEED_MIDS.get(market, 100.0)
        logger.warning(
            "Using SYNTHETIC orderbook feed (demo mode) — no live Drift data."
        )

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        mid = self._mids.get(market)
        if mid is None:
            mid = self._mids[market] = self._SEED_MIDS.get(market, 100.0)

        # Gentle random walk of the mid price.
        mid *= 1 + self._rng.uniform(-0.0005, 0.0005)
        self._mids[market] = mid

        tick = max(round(mid * 0.0005, 6), 0.01)
        depth = self.settings.orderbook_depth

        inject_wall = self._rng.random() < 0.25
        wall_size = round(self._rng.uniform(50, 200), 2)
        wall_side = self._rng.choice(("bids", "asks"))
        wall_levels = max(self.settings.layering_min_levels,
                          self.settings.repeated_min_count)

        def build_side(side: str, sign: int) -> list[Level]:
            levels: list[Level] = []
            for i in range(1, depth + 1):
                # Randomly drop levels to create flicker on the grid.
                if self._rng.random() < 0.15:
                    continue
                price = round(mid + sign * i * tick, 6)
                if inject_wall and side == wall_side and i <= wall_levels:
                    size = wall_size
                else:
                    size = round(self._rng.uniform(0.5, 40), 2)
                levels.append(Level(price, size))
            return levels

        bids = build_side("bids", -1)
        asks = build_side("asks", +1)
        return OrderbookSnapshot(
            market=market, timestamp=time.time(), bids=bids, asks=asks
        )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
async def create_feed(settings) -> OrderbookFeed:
    """Return a connected feed, preferring the real Drift feed.

    Falls back to the synthetic feed (with a clear warning) when driftpy is
    missing or the connection cannot be established.
    """
    try:
        feed: OrderbookFeed = DriftOrderbookFeed(settings)
        await feed.connect()
        return feed
    except Exception as exc:  # ImportError, connection errors, etc.
        logger.warning("Real Drift feed unavailable (%s); falling back.", exc)
        synthetic = SyntheticOrderbookFeed(settings)
        await synthetic.connect()
        return synthetic
