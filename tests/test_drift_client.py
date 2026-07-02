"""
tests/test_drift_client.py

Regression tests for DriftStack's connection lifecycle. driftpy itself isn't
a test dependency, so these exercise DriftStack directly with mock
subscriber/connection objects rather than going through build().
"""

import asyncio
import sys
import types

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


class _FakeSettings:
    rpc_url = "https://example.invalid"
    drift_env = "mainnet"
    keypair_path = ""
    update_frequency_ms = 1000


def _install_fake_driftpy_modules(monkeypatch, user_map_subscribe):
    """Install minimal fake modules so build()'s local imports succeed,
    letting us exercise the real subscribe-sequence/teardown wiring in
    build() itself rather than just _teardown() in isolation."""

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            self.closed = False

        async def close(self):
            self.closed = True

    class FakeKeypair:
        def __init__(self, *a, **k):
            pass

    class FakeWallet:
        def __init__(self, *a, **k):
            pass

    class FakeDriftClient:
        def __init__(self, *a, **k):
            self.unsubscribed = False

        async def subscribe(self):
            return None

        async def unsubscribe(self):
            self.unsubscribed = True

    class FakeAccountSubscriptionConfig:
        def __init__(self, *a, **k):
            pass

    class FakeUserMap:
        def __init__(self, *a, **k):
            self.unsubscribed = False

        async def subscribe(self):
            await user_map_subscribe(self)

        async def unsubscribe(self):
            self.unsubscribed = True

    class FakeUserMapConfig:
        def __init__(self, *a, **k):
            pass

    class FakeWebsocketConfig:
        def __init__(self, *a, **k):
            pass

    class FakeSlotSubscriber:
        def __init__(self, *a, **k):
            self.unsubscribed = False

        async def subscribe(self):
            return None

        async def unsubscribe(self):
            self.unsubscribed = True

    class FakeDLOBClientConfig:
        def __init__(self, *a, **k):
            pass

    class FakeDLOBSubscriber:
        def __init__(self, *a, **k):
            self.unsubscribed = False

        async def subscribe(self):
            return None

        async def unsubscribe(self):
            self.unsubscribed = True

    modules = {
        name: types.ModuleType(name)
        for name in (
            "solana",
            "solana.rpc",
            "solana.rpc.async_api",
            "solders",
            "solders.keypair",
            "anchorpy",
            "driftpy",
            "driftpy.drift_client",
            "driftpy.account_subscription_config",
            "driftpy.user_map",
            "driftpy.user_map.user_map",
            "driftpy.user_map.user_map_config",
            "driftpy.dlob",
            "driftpy.dlob.dlob_subscriber",
            "driftpy.dlob.client_types",
            "driftpy.slot",
            "driftpy.slot.slot_subscriber",
        )
    }
    modules["solana.rpc.async_api"].AsyncClient = FakeAsyncClient
    modules["solders.keypair"].Keypair = FakeKeypair
    modules["anchorpy"].Wallet = FakeWallet
    modules["driftpy.drift_client"].DriftClient = FakeDriftClient
    modules["driftpy.account_subscription_config"].AccountSubscriptionConfig = (
        FakeAccountSubscriptionConfig
    )
    modules["driftpy.user_map.user_map"].UserMap = FakeUserMap
    modules["driftpy.user_map.user_map_config"].UserMapConfig = FakeUserMapConfig
    modules["driftpy.user_map.user_map_config"].WebsocketConfig = FakeWebsocketConfig
    modules["driftpy.dlob.dlob_subscriber"].DLOBSubscriber = FakeDLOBSubscriber
    modules["driftpy.dlob.client_types"].DLOBClientConfig = FakeDLOBClientConfig
    modules["driftpy.slot.slot_subscriber"].SlotSubscriber = FakeSlotSubscriber

    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)


def test_build_tears_down_user_map_when_its_own_subscribe_partially_fails(monkeypatch):
    # Regression: build() only registered each object into `built` *after*
    # its own subscribe() call returned successfully. If subscribe() itself
    # raised after partially opening its websocket/background listener (not
    # some *other* object failing), the object was never captured in
    # `built` and _teardown() could never unsubscribe/close it — a silent
    # connection leak on a realistic flaky-RPC failure mode, since
    # create_feed() swallows this exception and falls back to the
    # synthetic feed.
    captured = {}

    async def flaky_user_map_subscribe(self):
        captured["user_map"] = self
        raise TimeoutError("user_map subscribe timed out")

    _install_fake_driftpy_modules(monkeypatch, flaky_user_map_subscribe)

    with pytest.raises(TimeoutError):
        asyncio.run(DriftStack.build(_FakeSettings()))

    assert captured["user_map"].unsubscribed is True
