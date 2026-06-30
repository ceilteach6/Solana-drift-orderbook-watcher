"""
src/collector/phoenix_client.py

Sets up a *read-only* connection to Phoenix (Ellipsis Labs' crankless,
fully on-chain CLOB on Solana) using the ``phoenix-trade`` Python package
(import name ``phoenix``, repo: github.com/Ellipsis-Labs/phoenixpy), and
exposes L2 orderbook snapshots.

Phoenix is the second on-chain Solana orderbook this project supports,
alongside Drift's DLOB (see docs/NOTES.md §4 — "other Solana venues"
expansion). The detector/risk/storage/dashboard layers do not know which
venue produced a snapshot; they only consume ``OrderbookSnapshot``.

This module is imported lazily by :class:`PhoenixOrderbookFeed`; if
``phoenix-trade`` (or its dependencies) is not installed, the import raises
and the caller falls back to the synthetic feed, same as the Drift path.

Unlike Drift, fetching the L2 book needs no trader keypair at all — Phoenix
markets are addressed by pubkey and ``get_l2_book`` is a plain read.
``MARKETS`` must therefore hold Phoenix market pubkeys (base58) when
``VENUE=phoenix``, e.g. SOL/USDC: ``4DoNfFBfF7UokCC2FQzriy7yHK6DY6NVdYpuekQ5pRgg``
(see config.example.env).

NOTE: like ``drift_client.py``, this follows the SDK surface as published in
the upstream repo at the time of writing. If your installed ``phoenix-trade``
version differs, adjust the calls below — the rest of the watcher only
depends on ``get_l2(market, depth)`` returning an object with
``bids`` / ``asks`` lists of ``price`` / ``size``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PhoenixStack:
    """Owns the Phoenix client lifecycle (read-only, no signing)."""

    def __init__(self, phoenix_client, market_pubkeys: dict) -> None:
        self._phoenix_client = phoenix_client
        self._market_pubkeys = market_pubkeys  # market string -> Pubkey

    @classmethod
    async def build(cls, settings) -> "PhoenixStack":
        """Connect read-only to Phoenix over the configured RPC and
        register every configured market (each addressed by pubkey)."""
        # Imported locally so a missing phoenix-trade turns into a clean
        # ImportError at call time (handled by the feed factory).
        from solders.pubkey import Pubkey

        from phoenix.client import PhoenixClient

        phoenix_client = PhoenixClient(custom_url=settings.rpc_url)

        market_pubkeys: dict = {}
        for market in settings.markets:
            pubkey = Pubkey.from_string(market)
            await phoenix_client.add_market(pubkey)
            market_pubkeys[market] = pubkey

        return cls(phoenix_client, market_pubkeys)

    async def get_l2(self, market: str, depth: int = 20):
        """Return the current L2 ladder for ``market`` (a configured pubkey)."""
        pubkey = self._market_pubkeys.get(market)
        if pubkey is None:
            logger.warning("Market %r was not registered via add_market()", market)
            return None
        return await self._phoenix_client.get_l2_book(pubkey, levels=depth)

    async def close(self) -> None:
        try:
            await self._phoenix_client.close()
        except Exception:  # pragma: no cover
            logger.debug("Error closing Phoenix client", exc_info=True)
