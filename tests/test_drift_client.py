"""
tests/test_drift_client.py

Tests ``DriftStack``'s teardown logic in isolation from driftpy/the network:
``close()``/``_teardown()`` only depend on plain Python objects exposing
``unsubscribe()`` or ``close()``, so they can be exercised with fakes.

Covers the resource-leak fix: every subscriber acquired in ``build()`` (incl.
``user_map`` and ``slot_subscriber``, previously dropped on the floor) must be
torn down, and a failure partway through teardown must not stop the rest from
being attempted.
"""

import asyncio

from src.collector.drift_client import DriftStack


class FakeSubscriber:
    def __init__(self, name, raise_on_unsubscribe=False):
        self.name = name
        self.unsubscribed = False
        self._raise = raise_on_unsubscribe

    def unsubscribe(self):
        if self._raise:
            raise RuntimeError(f"{self.name} teardown failed")
        self.unsubscribed = True


class FakeAsyncSubscriber(FakeSubscriber):
    async def unsubscribe(self):
        if self._raise:
            raise RuntimeError(f"{self.name} teardown failed")
        self.unsubscribed = True


class FakeConnection:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def make_stack(*, drift_client=None, dlob=None, user_map=None, slot=None, connection=None):
    return DriftStack(
        drift_client or FakeSubscriber("drift_client"),
        dlob or FakeSubscriber("dlob"),
        user_map or FakeSubscriber("user_map"),
        slot or FakeSubscriber("slot_subscriber"),
        connection or FakeConnection(),
    )


def test_close_unsubscribes_all_four_subscribers_and_closes_connection():
    drift_client = FakeSubscriber("drift_client")
    dlob = FakeAsyncSubscriber("dlob")
    user_map = FakeSubscriber("user_map")
    slot = FakeSubscriber("slot_subscriber")
    connection = FakeConnection()
    stack = make_stack(
        drift_client=drift_client, dlob=dlob, user_map=user_map, slot=slot, connection=connection
    )

    asyncio.run(stack.close())

    assert drift_client.unsubscribed
    assert dlob.unsubscribed
    assert user_map.unsubscribed  # previously leaked — never stored/unsubscribed
    assert slot.unsubscribed  # previously leaked — never stored/unsubscribed
    assert connection.closed


def test_close_is_best_effort_one_failure_does_not_block_the_rest():
    drift_client = FakeSubscriber("drift_client")
    dlob = FakeSubscriber("dlob")
    user_map = FakeSubscriber("user_map", raise_on_unsubscribe=True)
    slot = FakeSubscriber("slot_subscriber")
    connection = FakeConnection()
    stack = make_stack(
        drift_client=drift_client, dlob=dlob, user_map=user_map, slot=slot, connection=connection
    )

    asyncio.run(stack.close())  # must not raise

    assert drift_client.unsubscribed
    assert dlob.unsubscribed
    assert slot.unsubscribed
    assert connection.closed  # still closed despite user_map failing


def test_teardown_handles_objects_without_unsubscribe():
    # Objects that are neither a connection (close, no unsubscribe) nor a
    # subscriber (unsubscribe) must be skipped silently, not crash teardown.
    asyncio.run(DriftStack._teardown([object()]))
