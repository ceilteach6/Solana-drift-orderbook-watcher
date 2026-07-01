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

import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


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
class StaleFeedError(RuntimeError):
    """Raised when the DLOB hasn't changed for longer than expected.

    driftpy's ``DLOBSubscriber`` refreshes its cached book from a background
    task; if that task dies (or never got scheduled), ``get_l2`` keeps
    returning the same book forever with no error of its own — every poll
    "succeeds", so nothing else notices. This turns that silent staleness
    into a normal per-tick failure the watcher already logs and recovers from.
    """


class DriftOrderbookFeed(OrderbookFeed):
    """Reads L2 orderbooks from the live Drift DLOB via ``driftpy``."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._stack = None  # populated in connect()
        self._last_book: dict[str, tuple] = {}
        self._last_change_ts: dict[str, float] = {}

    async def connect(self) -> None:
        # Imported lazily so the package works without driftpy installed.
        from src.collector.drift_client import DriftStack

        self._stack = await DriftStack.build(self.settings)
        logger.info("Connected to Drift (%s)", self.settings.drift_env)

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        if self._stack is None:
            raise RuntimeError("connect() must be called before get_snapshot()")
        raw = self._stack.get_l2(market, depth=self.settings.orderbook_depth)
        if raw is None:
            return None
        snapshot = _snapshot_from_driftpy(market, raw)
        self._check_staleness(market, snapshot)
        return snapshot

    def _check_staleness(self, market: str, snapshot: OrderbookSnapshot) -> None:
        max_stale = self.settings.dlob_stale_after_sec
        if max_stale <= 0:
            return
        now = time.monotonic()
        book_key = (
            tuple((lvl.price, lvl.size) for lvl in snapshot.bids),
            tuple((lvl.price, lvl.size) for lvl in snapshot.asks),
        )
        if book_key != self._last_book.get(market):
            self._last_book[market] = book_key
            self._last_change_ts[market] = now
            return
        last_change = self._last_change_ts.setdefault(market, now)
        stale_for = now - last_change
        if stale_for > max_stale:
            raise StaleFeedError(
                f"{market} DLOB unchanged for {stale_for:.0f}s "
                f"(limit {max_stale:.0f}s) — the background DLOB update loop "
                f"may have died; check driftpy connectivity."
            )

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.close()


try:
    # Drift fixed-point scaling: prices are integers scaled by 1e6, base
    # asset amounts (sizes) by 1e9. driftpy exposes these as constants, but
    # we don't hard-depend on the package being installed/importable.
    from driftpy.constants.numeric_constants import BASE_PRECISION, PRICE_PRECISION
except ImportError:  # pragma: no cover - exercised when driftpy is absent
    PRICE_PRECISION = 1_000_000
    BASE_PRECISION = 1_000_000_000


def _to_float(value, precision: int) -> float:
    """driftpy L2 levels carry fixed-point integers scaled by ``precision``.

    e.g. a $150 price is the raw int 150_000_000 (PRICE_PRECISION=1e6); a
    10-token size is the raw int 10_000_000_000 (BASE_PRECISION=1e9).
    """
    try:
        raw = float(value)
    except (TypeError, ValueError):
        raw = float(getattr(value, "value", 0))
    return raw / precision


def _snapshot_from_driftpy(market: str, l2) -> OrderbookSnapshot:
    def levels(side) -> list[Level]:
        out: list[Level] = []
        for lvl in side or []:
            price = getattr(lvl, "price", None)
            size = getattr(lvl, "size", None)
            if price is None and isinstance(lvl, dict):
                price, size = lvl.get("price"), lvl.get("size")
            out.append(Level(_to_float(price, PRICE_PRECISION), _to_float(size, BASE_PRECISION)))
        return out

    # Every consumer (mid price, detectors, storage) assumes bids[0]/asks[0]
    # is the best price. driftpy's L2 helper does not document an ordering
    # guarantee, so sort explicitly rather than trust upstream output.
    bids = sorted(levels(getattr(l2, "bids", None) or []), key=lambda lvl: lvl.price, reverse=True)
    asks = sorted(levels(getattr(l2, "asks", None) or []), key=lambda lvl: lvl.price)
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
