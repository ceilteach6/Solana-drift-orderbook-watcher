"""
tests/test_drift_client.py

Regression test for DriftStack's teardown: ``build()`` creates a UserMap and
a SlotSubscriber alongside the DLOB subscriber, and ``close()`` must
unsubscribe all of them, not just the DLOB subscriber and the DriftClient.
Leaving the user_map/slot_subscriber subscriptions open is a resource leak
(dangling websocket tasks) every time the watcher reconnects or shuts down.
Network-free: driftpy objects are stubbed.
"""

import asyncio

from src.collector.drift_client import DriftStack


class _StubSub:
    def __init__(self):
        self.unsubscribed = False

    async def unsubscribe(self):
        self.unsubscribed = True


class _StubConnection:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_close_unsubscribes_user_map_and_slot_subscriber():
    dlob = _StubSub()
    drift_client = _StubSub()
    user_map = _StubSub()
    slot_subscriber = _StubSub()
    connection = _StubConnection()

    stack = DriftStack(
        drift_client, dlob, connection,
        user_map=user_map, slot_subscriber=slot_subscriber,
    )
    asyncio.run(stack.close())

    assert dlob.unsubscribed
    assert drift_client.unsubscribed
    assert user_map.unsubscribed
    assert slot_subscriber.unsubscribed
    assert connection.closed


def test_close_tolerates_missing_user_map_and_slot_subscriber():
    # build() may not have reached the user_map/slot_subscriber steps if it
    # failed partway through -- close() must not crash on the Nones.
    dlob = _StubSub()
    drift_client = _StubSub()
    connection = _StubConnection()

    stack = DriftStack(drift_client, dlob, connection)
    asyncio.run(stack.close())  # must not raise

    assert dlob.unsubscribed
    assert drift_client.unsubscribed
    assert connection.closed
