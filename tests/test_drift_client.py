"""
tests/test_drift_client.py

Tests DriftStack.close() teardown without needing driftpy installed: build()
is the only method that imports driftpy, close() only depends on the objects
handed to __init__.
"""

import asyncio

from src.collector.drift_client import DriftStack


class _FakeSub:
    def __init__(self, *, async_unsubscribe=False):
        self.unsubscribed = False
        self._async = async_unsubscribe

    def unsubscribe(self):
        self.unsubscribed = True
        if self._async:
            async def _noop():
                return None

            return _noop()
        return None


class _FakeConnection:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_close_unsubscribes_every_subscriber_including_user_map_and_slot_subscriber():
    dlob = _FakeSub()
    drift_client = _FakeSub(async_unsubscribe=True)
    user_map = _FakeSub()
    slot_subscriber = _FakeSub(async_unsubscribe=True)
    connection = _FakeConnection()

    stack = DriftStack(drift_client, dlob, connection, user_map, slot_subscriber)
    asyncio.run(stack.close())

    assert dlob.unsubscribed
    assert drift_client.unsubscribed
    assert user_map.unsubscribed  # previously leaked: never stored on self
    assert slot_subscriber.unsubscribed  # previously leaked: never stored on self
    assert connection.closed


def test_close_tolerates_missing_user_map_and_slot_subscriber():
    dlob = _FakeSub()
    drift_client = _FakeSub()
    connection = _FakeConnection()

    stack = DriftStack(drift_client, dlob, connection)  # defaults: no user_map/slot_subscriber
    asyncio.run(stack.close())  # must not raise

    assert dlob.unsubscribed
    assert drift_client.unsubscribed
    assert connection.closed
