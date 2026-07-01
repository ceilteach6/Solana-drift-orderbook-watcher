"""
src/collector/hyperliquid_feed.py

Read-only orderbook feed for Hyperliquid perpetual markets.
Uses the public Hyperliquid REST API — no SDK, no authentication required.

Market names follow Hyperliquid's convention ("BTC", "ETH", "SOL", …).
Set NETWORK=hyperliquid and MARKETS=BTC,ETH,SOL in your .env.

API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from src.collector.orderbook_feed import Level, OrderbookFeed, OrderbookSnapshot

logger = logging.getLogger(__name__)

_API_URL = "https://api.hyperliquid.xyz/info"


class HyperliquidOrderbookFeed(OrderbookFeed):
    """Polls Hyperliquid L2 orderbook snapshots via the public REST API.

    The feed is stateless — each call to ``get_snapshot`` issues a single
    HTTP POST. ``urllib`` is blocking, so requests run on a dedicated
    executor (mirroring :class:`DriftOrderbookFeed`) rather than on the
    event loop, so a slow/stuck request only stalls this feed's own polling
    instead of every other market's ticks, health-checks, and alerts.
    """

    def __init__(self, settings) -> None:
        self.settings = settings
        self._executor = ThreadPoolExecutor(
            max_workers=max(4, len(settings.markets)), thread_name_prefix="hl-l2"
        )

    async def connect(self) -> None:
        """Verify API reachability with a lightweight metadata probe."""
        try:
            await self._request({"type": "meta"})
            logger.info("Connected to Hyperliquid REST API (%s)", _API_URL)
        except Exception as exc:
            raise RuntimeError(f"Hyperliquid API unreachable: {exc}") from exc

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        # HL uses plain coin symbols; strip a trailing "-PERP" if callers use
        # Drift-style names. "BTC-PERP" -> "BTC", "SOL" -> "SOL".
        coin = market.removesuffix("-PERP")
        try:
            raw = await self._request({"type": "l2Book", "coin": coin})
        except Exception as exc:
            logger.warning("Hyperliquid L2 book failed for %s: %s", coin, exc)
            return None
        return _parse_l2(market, raw)

    async def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _request(self, payload: dict) -> dict:
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, self._blocking_request, payload),
            timeout=self.settings.snapshot_timeout_sec,
        )

    @staticmethod
    def _blocking_request(payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            _API_URL,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (fixed HTTPS URL, no user input)
            return json.loads(resp.read())


def _parse_l2(market: str, raw: dict) -> OrderbookSnapshot | None:
    """Convert a raw Hyperliquid L2 response to a normalized snapshot."""
    levels_raw = raw.get("levels") if isinstance(raw, dict) else None
    if not levels_raw or len(levels_raw) < 2:
        logger.debug("Unexpected Hyperliquid L2 payload for %s: %r", market, raw)
        return None

    def parse_side(side_raw: list) -> list[Level]:
        out: list[Level] = []
        for lvl in side_raw:
            try:
                out.append(Level(float(lvl["px"]), float(lvl["sz"])))
            except (KeyError, ValueError, TypeError):
                continue
        return out

    # HL returns asks (ascending) first, then bids (descending) — matches the
    # ordering every downstream consumer (mid price, imbalance detector,
    # sqlite_store) already assumes for bids-desc/asks-asc, but sort
    # defensively anyway rather than trust an external API's ordering.
    asks = sorted(parse_side(levels_raw[0]), key=lambda lvl: lvl.price)
    bids = sorted(parse_side(levels_raw[1]), key=lambda lvl: lvl.price, reverse=True)
    return OrderbookSnapshot(market=market, timestamp=time.time(), bids=bids, asks=asks)
