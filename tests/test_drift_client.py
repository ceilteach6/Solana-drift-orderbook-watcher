"""
tests/test_drift_client.py

Regression test for DriftStack teardown: every resource subscribed in
build() must be reachable from close()/`_teardown`, including ones added
after the original close() only knew about `_dlob`/`_drift_client`.
"""

import asyncio

from src.collector.drift_client import DriftStack


class _FakeResource:
    def __init__(self, name):
        self.name = name
        self.unsubscribed = False

    async def unsubscribe(self):
        self.unsubscribed = True


class _FakeConnection:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_close_unsubscribes_every_resource():
    connection = _FakeConnection()
    drift_client = _FakeResource("drift_client")
    user_map = _FakeResource("user_map")
    slot_subscriber = _FakeResource("slot_subscriber")
    dlob = _FakeResource("dlob")

    stack = DriftStack(drift_client, dlob, connection, user_map, slot_subscriber)
    asyncio.run(stack.close())

    assert drift_client.unsubscribed
    assert user_map.unsubscribed
    assert slot_subscriber.unsubscribed
    assert dlob.unsubscribed
    assert connection.closed


def test_teardown_tolerates_partially_built_stack():
    """Simulates build() failing after only drift_client subscribed."""
    connection = _FakeConnection()
    drift_client = _FakeResource("drift_client")

    asyncio.run(DriftStack._teardown(connection, drift_client, None, None, None))

    assert drift_client.unsubscribed
    assert connection.closed
