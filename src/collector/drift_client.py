"""
src/collector/drift_client.py

Sets up a *read-only* Drift connection and DLOB subscriber using ``driftpy``,
and exposes synchronous L2 orderbook snapshots.

This module is imported lazily by :class:`DriftOrderbookFeed`; if ``driftpy``
(or its dependencies) is not installed, the import raises and the caller falls
back to the synthetic feed.

NOTE: ``driftpy``'s public API has shifted across releases. The setup below
follows the layering documented in the project README (OrderSubscriber ->
DLOBSubscriber). If your installed ``driftpy`` version differs, adjust the
imports/constructors here — the rest of the watcher only depends on
``get_l2(market, depth)`` returning an object with ``bids`` / ``asks``.
"""

from __future__ import annotations

import inspect
import logging

logger = logging.getLogger(__name__)


class DriftStack:
    """Owns the DriftClient + DLOB subscriber lifecycle."""

    def __init__(
        self, drift_client, dlob_subscriber, connection, user_map=None, slot_subscriber=None
    ) -> None:
        self._drift_client = drift_client
        self._dlob = dlob_subscriber
        self._connection = connection
        # Also subscribed in build() — stored so close() can unsubscribe them
        # too; otherwise their websocket listeners leak as orphaned background
        # tasks after every "clean" shutdown/reconnect.
        self._user_map = user_map
        self._slot_subscriber = slot_subscriber

    @classmethod
    async def build(cls, settings) -> "DriftStack":
        """Connect read-only and start the DLOB subscriber."""
        # Imports are local so a missing driftpy turns into a clean ImportError
        # at call time (handled by the feed factory).
        from solana.rpc.async_api import AsyncClient
        from solders.keypair import Keypair
        from anchorpy import Wallet

        from driftpy.drift_client import DriftClient
        from driftpy.account_subscription_config import AccountSubscriptionConfig
        from driftpy.user_map.user_map import UserMap
        from driftpy.user_map.user_map_config import UserMapConfig, WebsocketConfig
        from driftpy.dlob.dlob_subscriber import DLOBSubscriber
        from driftpy.dlob.client_types import DLOBClientConfig
        from driftpy.slot.slot_subscriber import SlotSubscriber

        connection = AsyncClient(settings.rpc_url)

        # Everything built so far, so a failure partway through the subscribe
        # sequence below can tear down what already succeeded instead of
        # leaking an open RPC connection / websocket listeners forever (the
        # caller's fallback to the synthetic feed swallows this exception
        # without ever getting a DriftStack to close()).
        built: dict = {"connection": connection}
        try:
            # An ephemeral keypair is enough to *watch* — no funds, no signing.
            if settings.keypair_path:
                with open(settings.keypair_path) as fh:
                    import json

                    kp = Keypair.from_bytes(bytes(json.load(fh)))
            else:
                kp = Keypair()
            wallet = Wallet(kp)

            drift_client = DriftClient(
                connection,
                wallet,
                settings.drift_env,
                account_subscription=AccountSubscriptionConfig("websocket"),
            )
            await drift_client.subscribe()
            built["drift_client"] = drift_client

            user_map = UserMap(UserMapConfig(drift_client, WebsocketConfig()))
            await user_map.subscribe()
            built["user_map"] = user_map

            slot_subscriber = SlotSubscriber(drift_client)
            await slot_subscriber.subscribe()
            built["slot_subscriber"] = slot_subscriber

            dlob_config = DLOBClientConfig(
                drift_client, user_map, slot_subscriber, settings.update_frequency_ms
            )
            dlob_subscriber = DLOBSubscriber(config=dlob_config)
            await dlob_subscriber.subscribe()
            built["dlob_subscriber"] = dlob_subscriber
        except Exception:
            await cls._teardown(built)
            raise

        return cls(
            built["drift_client"],
            built["dlob_subscriber"],
            connection,
            user_map=built["user_map"],
            slot_subscriber=built["slot_subscriber"],
        )

    def get_l2(self, market: str, depth: int = 20):
        """Return the current L2 orderbook for ``market`` (driftpy object)."""
        return self._dlob.get_l2_orderbook_sync(market, depth=depth)

    @staticmethod
    async def _teardown(built: dict) -> None:
        """Best-effort unsubscribe/close of whatever ``build()`` had already
        constructed before it failed."""
        for key in ("dlob_subscriber", "slot_subscriber", "user_map", "drift_client"):
            obj = built.get(key)
            if obj is None:
                continue
            unsub = getattr(obj, "unsubscribe", None)
            if unsub is None:
                continue
            try:
                result = unsub()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # pragma: no cover - best-effort teardown
                logger.debug("Error during teardown of %r", obj, exc_info=True)
        connection = built.get("connection")
        if connection is not None:
            try:
                await connection.close()
            except Exception:  # pragma: no cover
                logger.debug("Error closing RPC connection", exc_info=True)

    async def close(self) -> None:
        for obj in (self._dlob, self._slot_subscriber, self._user_map, self._drift_client):
            if obj is None:
                continue
            unsub = getattr(obj, "unsubscribe", None)
            if unsub is None:
                continue
            try:
                result = unsub()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # pragma: no cover - best-effort teardown
                logger.debug("Error during teardown of %r", obj, exc_info=True)
        try:
            await self._connection.close()
        except Exception:  # pragma: no cover
            logger.debug("Error closing RPC connection", exc_info=True)
