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


def _load_keypair(keypair_path: str, Keypair):
    """Load an ephemeral watch-only keypair, with a clear error on failure.

    A malformed or unreadable key file should not surface as a bare
    ``FileNotFoundError``/``json.JSONDecodeError`` deep inside driftpy setup —
    wrap it so the operator immediately knows which setting is at fault.
    """
    import json

    try:
        with open(keypair_path) as fh:
            raw = json.load(fh)
        return Keypair.from_bytes(bytes(raw))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load KEYPAIR_PATH={keypair_path!r}: {exc}. "
            "An ephemeral/empty wallet is enough to watch — leave KEYPAIR_PATH "
            "unset to generate one automatically."
        ) from exc


class DriftStack:
    """Owns the DriftClient + DLOB subscriber lifecycle."""

    def __init__(
        self, drift_client, dlob_subscriber, connection, user_map=None, slot_subscriber=None
    ) -> None:
        self._drift_client = drift_client
        self._dlob = dlob_subscriber
        self._connection = connection
        self._user_map = user_map
        self._slot_subscriber = slot_subscriber

    @classmethod
    async def build(cls, settings) -> "DriftStack":
        """Connect read-only and start the DLOB subscriber.

        Every websocket subscription opened here is torn down again if a
        later step fails, so a partial connect (e.g. ``user_map`` subscribes
        but ``slot_subscriber`` then errors) never leaks an open subscription
        that nobody holds a reference to.
        """
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
        opened: list = []  # opened subscriptions, oldest first, for teardown on failure

        try:
            # An ephemeral keypair is enough to *watch* — no funds, no signing.
            kp = (
                _load_keypair(settings.keypair_path, Keypair)
                if settings.keypair_path
                else Keypair()
            )
            wallet = Wallet(kp)

            drift_client = DriftClient(
                connection,
                wallet,
                settings.drift_env,
                account_subscription=AccountSubscriptionConfig("websocket"),
            )
            await drift_client.subscribe()
            opened.append(drift_client)

            user_map = UserMap(UserMapConfig(drift_client, WebsocketConfig()))
            await user_map.subscribe()
            opened.append(user_map)

            slot_subscriber = SlotSubscriber(drift_client)
            await slot_subscriber.subscribe()
            opened.append(slot_subscriber)

            dlob_config = DLOBClientConfig(
                drift_client, user_map, slot_subscriber, settings.update_frequency_ms
            )
            dlob_subscriber = DLOBSubscriber(config=dlob_config)
            await dlob_subscriber.subscribe()
            opened.append(dlob_subscriber)
        except Exception:
            await _teardown(opened, connection)
            raise

        return cls(drift_client, dlob_subscriber, connection, user_map, slot_subscriber)

    def get_l2(self, market: str, depth: int = 20):
        """Return the current L2 orderbook for ``market`` (driftpy object)."""
        return self._dlob.get_l2_orderbook_sync(market, depth=depth)

    async def close(self) -> None:
        await _teardown(
            (self._dlob, self._user_map, self._slot_subscriber, self._drift_client),
            self._connection,
        )


async def _teardown(subscriptions, connection) -> None:
    """Best-effort unsubscribe of every (possibly ``None``) subscription, then
    close the RPC connection. Each step is independent so one failure does not
    prevent the rest from running."""
    for obj in subscriptions:
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
        await connection.close()
    except Exception:  # pragma: no cover
        logger.debug("Error closing RPC connection", exc_info=True)
