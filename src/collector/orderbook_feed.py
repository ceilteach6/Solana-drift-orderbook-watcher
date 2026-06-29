"""
src/collector/orderbook_feed.py

Normalized L2 orderbook model + feed implementations.

Two feeds are provided:

* :class:`DriftOrderbookFeed` — the real feed, backed by the ``driftpy`` DLOB
  subscriber (see :mod:`src.collector.drift_client`).
* :class:`SyntheticOrderbookFeed` — a self-contained, network-free generator
  used as a fallback and for examples/tests.  It produces plausible orderbooks
  and occasionally injects bot-like patterns so the detectors have something to
  react to.

``create_feed()`` tries the real feed first and transparently falls back to the
synthetic one if ``driftpy`` is unavailable or the connection fails.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Normalized data model
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

    async def connect(self) -> None:
        return None

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Real feed (driftpy DLOB)
# --------------------------------------------------------------------------- #
class DriftOrderbookFeed(OrderbookFeed):
    """Reads L2 orderbooks from the live Drift DLOB via ``driftpy``."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self._stack = None

    async def connect(self) -> None:
        from src.collector.drift_client import DriftStack

        self._stack = await DriftStack.build(self.settings)
        logger.info("Connected to Drift (%s)", self.settings.drift_env)

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        if self._stack is None:
            raise RuntimeError("connect() must be called first")
        raw = self._stack.get_l2(market, depth=self.settings.orderbook_depth)
        if raw is None:
            return None
        return _snapshot_from_driftpy(market, raw)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.close()


def _to_float(value) -> float:
    """Coerce a driftpy L2 level field to a plain Python float.

    driftpy's L2 helpers typically pre-divide by PRICE_PRECISION (1e6) and
    BASE_PRECISION (1e9) so values are already human-readable.  We fall back
    to ``getattr(value, "value", 0)`` for SDK versions that still expose the
    raw integer via a ``value`` attribute.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(getattr(value, "value", 0))


def _snapshot_from_driftpy(market: str, l2) -> OrderbookSnapshot:
    def levels(side) -> list[Level]:
        out: list[Level] = []
        for lvl in side or []:
            price = getattr(lvl, "price", None)
            size = getattr(lvl, "size", None)
            if price is None and isinstance(lvl, dict):
                price, size = lvl.get("price"), lvl.get("size")
            out.append(Level(_to_float(price), _to_float(size)))
        return out

    bids = levels(getattr(l2, "bids", None) or [])
    asks = levels(getattr(l2, "asks", None) or [])
    return OrderbookSnapshot(market=market, timestamp=time.time(), bids=bids, asks=asks)


# --------------------------------------------------------------------------- #
# Synthetic feed (network-free fallback / demo)
# --------------------------------------------------------------------------- #
class SyntheticOrderbookFeed(OrderbookFeed):
    """Generates plausible orderbooks on a fixed price grid.

    A fixed grid means price levels recur across snapshots, which lets the
    flicker detector observe levels appearing/disappearing.  With small
    probability it injects a repeated-size "wall" so the repeated-size and
    layering detectors fire too.
    """

    _SEED_MIDS: dict[str, float] = {
        "SOL-PERP": 150.0,
        "BTC-PERP": 65000.0,
        "ETH-PERP": 3400.0,
    }

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

        mid *= 1 + self._rng.uniform(-0.0005, 0.0005)
        self._mids[market] = mid

        tick = max(round(mid * 0.0005, 6), 0.01)
        depth = self.settings.orderbook_depth

        inject_wall = self._rng.random() < 0.25
        wall_size = round(self._rng.uniform(50, 200), 2)
        wall_side = self._rng.choice(("bids", "asks"))
        wall_levels = max(
            self.settings.layering_min_levels, self.settings.repeated_min_count
        )

        def build_side(side: str, sign: int) -> list[Level]:
            levels: list[Level] = []
            for i in range(1, depth + 1):
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
    except Exception as exc:
        logger.warning("Real Drift feed unavailable (%s); falling back to synthetic.", exc)
        synthetic = SyntheticOrderbookFeed(settings)
        await synthetic.connect()
        return synthetic
