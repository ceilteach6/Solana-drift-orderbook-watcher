"""
tests/test_drift_client.py

Regression test for DriftStack's teardown: user_map and slot_subscriber are
subscribed alongside dlob/drift_client in build() and must be unsubscribed in
close() too, or their websocket subscriptions (and the background tasks
driving them) outlive the watcher on shutdown.

This only exercises DriftStack's __init__/close() with plain mocks -- it does
not import driftpy (not a project dependency in this environment, and
build()'s own docstring notes the driftpy API shifts across releases, so a
full mock of that import graph would be brittle for little value).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.collector.drift_client import DriftStack


def _mock_with_unsubscribe():
    obj = MagicMock()
    obj.unsubscribe = AsyncMock()
    return obj


def test_close_unsubscribes_every_subscribed_component():
    dlob = _mock_with_unsubscribe()
    user_map = _mock_with_unsubscribe()
    slot_subscriber = _mock_with_unsubscribe()
    drift_client = _mock_with_unsubscribe()
    connection = MagicMock()
    connection.close = AsyncMock()

    stack = DriftStack(
        drift_client, dlob, connection,
        user_map=user_map, slot_subscriber=slot_subscriber,
    )
    asyncio.run(stack.close())

    dlob.unsubscribe.assert_awaited_once()
    user_map.unsubscribe.assert_awaited_once()
    slot_subscriber.unsubscribe.assert_awaited_once()
    drift_client.unsubscribe.assert_awaited_once()
    connection.close.assert_awaited_once()


def test_close_tolerates_missing_user_map_and_slot_subscriber():
    dlob = _mock_with_unsubscribe()
    drift_client = _mock_with_unsubscribe()
    connection = MagicMock()
    connection.close = AsyncMock()

    stack = DriftStack(drift_client, dlob, connection)
    asyncio.run(stack.close())  # must not raise

    dlob.unsubscribe.assert_awaited_once()
    drift_client.unsubscribe.assert_awaited_once()
    connection.close.assert_awaited_once()


def test_close_survives_unsubscribe_errors():
    dlob = _mock_with_unsubscribe()
    dlob.unsubscribe.side_effect = RuntimeError("boom")
    user_map = _mock_with_unsubscribe()
    connection = MagicMock()
    connection.close = AsyncMock()

    stack = DriftStack(MagicMock(unsubscribe=None), dlob, connection, user_map=user_map)
    asyncio.run(stack.close())  # a failing unsubscribe must not block the rest

    user_map.unsubscribe.assert_awaited_once()
    connection.close.assert_awaited_once()
