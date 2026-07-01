"""
tests/test_drift_client.py

Regression tests for DriftStack's connection lifecycle. driftpy itself isn't
a test dependency, so these exercise DriftStack directly with mock
subscriber/connection objects rather than going through build().
"""

import asyncio

import pytest

from src.collector.drift_client import DriftStack


class FakeSubscriber:
    def __init__(self, fail=False):
        self.fail = fail
        self.unsubscribed = False

    async def unsubscribe(self):
        self.unsubscribed = True
        if self.fail:
            raise RuntimeError("boom")


class FakeConnection:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_close_unsubscribes_dlob_user_map_slot_subscriber_and_drift_client():
    # Regression: user_map/slot_subscriber used to be subscribed in build()
    # but never stored on the instance, so close() could never unsubscribe
    # them — their websocket listeners leaked as orphaned background tasks.
    dlob = FakeSubscriber()
    user_map = FakeSubscriber()
    slot_subscriber = FakeSubscriber()
    drift_client = FakeSubscriber()
    connection = FakeConnection()

    stack = DriftStack(drift_client, dlob, connection, user_map, slot_subscriber)
    asyncio.run(stack.close())

    assert dlob.unsubscribed
    assert user_map.unsubscribed
    assert slot_subscriber.unsubscribed
    assert drift_client.unsubscribed
    assert connection.closed


def test_close_tolerates_missing_user_map_and_slot_subscriber():
    # Constructed without them (e.g. an older caller) shouldn't crash close().
    dlob = FakeSubscriber()
    drift_client = FakeSubscriber()
    connection = FakeConnection()

    stack = DriftStack(drift_client, dlob, connection)
    asyncio.run(stack.close())

    assert dlob.unsubscribed
    assert connection.closed


def test_close_is_best_effort_when_one_unsubscribe_raises():
    dlob = FakeSubscriber(fail=True)
    user_map = FakeSubscriber()
    connection = FakeConnection()

    stack = DriftStack(None, dlob, connection, user_map, None)
    asyncio.run(stack.close())  # must not raise

    assert user_map.unsubscribed
    assert connection.closed


def test_teardown_unwinds_partially_built_stack_on_failure():
    # Regression: build() had no error handling around its sequence of
    # awaited subscribe() calls. A failure partway through (e.g. user_map
    # times out after drift_client already connected) used to leave the
    # already-open RPC connection/websocket listeners running forever.
    drift_client = FakeSubscriber()
    connection = FakeConnection()
    built = {"connection": connection, "drift_client": drift_client}

    asyncio.run(DriftStack._teardown(built))

    assert drift_client.unsubscribed
    assert connection.closed


def test_teardown_handles_empty_built_dict():
    asyncio.run(DriftStack._teardown({}))  # must not raise
