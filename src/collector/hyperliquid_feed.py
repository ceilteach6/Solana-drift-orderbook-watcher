"""
src/collector/hyperliquid_feed.py

Read-only orderbook feed for Hyperliquid perpetual markets.
Uses the public Hyperliquid REST API — no SDK, no authentication required.

Market names follow Hyperliquid's convention ("BTC", "ETH", "SOL", …).
Set NETWORK=hyperliquid and MARKETS=BTC,ETH,SOL in your .env.

API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request

from src.collector.orderbook_feed import Level, OrderbookFeed, OrderbookSnapshot

logger = logging.getLogger(__name__)

_API_URL = "https://api.hyperliquid.xyz/info"
_TIMEOUT = 10  # seconds


class HyperliquidOrderbookFeed(OrderbookFeed):
    """Polls Hyperliquid L2 orderbook snapshots via the public REST API.

    The feed is stateless — each call to ``get_snapshot`` issues a single
    HTTP POST and returns immediately.  This is appropriate for the watcher's
    polling model (UPDATE_FREQUENCY_MS interval).

    Hyperliquid L2 response layout::

        {"levels": [asks, bids]}

    where each entry is {"px": "43000.0", "sz": "0.5", "n": <num_orders>}.
    ``levels[0]`` = asks (ascending price), ``levels[1]`` = bids (descending).
    """

    def __init__(self, settings) -> None:
        self.settings = settings

    async def connect(self) -> None:
        """Verify API reachability with a lightweight metadata probe."""
        try:
            self._request({"type": "meta"})
            logger.info("Connected to Hyperliquid REST API (%s)", _API_URL)
        except Exception as exc:
            raise RuntimeError(f"Hyperliquid API unreachable: {exc}") from exc

    async def get_snapshot(self, market: str) -> OrderbookSnapshot | None:
        # HL uses plain coin symbols; strip a trailing "-PERP" if callers use
        # Drift-style names.  "BTC-PERP" → "BTC", "SOL" → "SOL".
        coin = market.removesuffix("-PERP")
        try:
            raw = self._request({"type": "l2Book", "coin": coin})
        except Exception as exc:
            logger.warning("Hyperliquid L2 book failed for %s: %s", coin, exc)
            return None
        return _parse_l2(market, raw)

    async def close(self) -> None:
        pass  # stateless REST client, nothing to close

    def _request(self, payload: dict) -> dict:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            _API_URL,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read())


def _parse_l2(market: str, raw: dict) -> OrderbookSnapshot | None:
    """Convert a raw Hyperliquid L2 response to a normalized snapshot."""
    levels_raw = raw.get("levels")
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

    # HL returns asks (ascending) first, then bids (descending).
    asks = parse_side(levels_raw[0])
    bids = parse_side(levels_raw[1])
    return OrderbookSnapshot(market=market, timestamp=time.time(), bids=bids, asks=asks)
