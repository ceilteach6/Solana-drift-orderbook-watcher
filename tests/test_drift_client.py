"""
tests/test_drift_client.py

``src/collector/drift_client.py`` only runs against the real ``driftpy``
package, which isn't a test dependency (no network, heavy install). That left
two real bugs invisible to the test suite for a long time:

1. ``DLOBClientConfig.update_frequency`` is consumed by driftpy as
   ``asyncio.sleep()`` *seconds*, but the code was passing the raw
   millisecond setting straight through — at the default 1000ms config, the
   live DLOB would only rebuild once every ~1000 seconds while the watcher
   kept polling it every second, silently serving a stale orderbook forever.
2. ``DriftStack.close()`` never tore down the ``UserMap``/``SlotSubscriber``
   it created in ``build()`` — their background websocket subscription tasks
   leaked on every reconnect.

These tests stub out the ``driftpy``/``solana``/``solders``/``anchorpy``
module surface that ``DriftStack.build()`` imports, so the wiring can be
exercised without the real (heavy, network-dependent) packages installed.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field

import pytest


@dataclass
class _Settings:
    rpc_url: str = "https://example.invalid"
    drift_env: str = "mainnet"
    keypair_path: str = ""
    update_frequency_ms: int = 1000


class _Recorder:
    """Shared call log so assertions can inspect what the stack did."""

    def __init__(self):
        self.dlob_config_args = None
        self.unsubscribed = []
        self.connection_closed = False


@pytest.fixture
def driftpy_stubs(monkeypatch):
    rec = _Recorder()

    # --- solana / solders / anchorpy ------------------------------------- #
    class FakeAsyncClient:
        def __init__(self, url):
            self.url = url

        async def close(self):
            rec.connection_closed = True

    class FakeKeypair:
        def __init__(self):
            pass

        @classmethod
        def from_bytes(cls, b):
            return cls()

    class FakeWallet:
        def __init__(self, kp):
            self.kp = kp

    solana_mod = types.ModuleType("solana")
    solana_rpc_mod = types.ModuleType("solana.rpc")
    solana_rpc_async_api_mod = types.ModuleType("solana.rpc.async_api")
    solana_rpc_async_api_mod.AsyncClient = FakeAsyncClient

    solders_mod = types.ModuleType("solders")
    solders_keypair_mod = types.ModuleType("solders.keypair")
    solders_keypair_mod.Keypair = FakeKeypair

    anchorpy_mod = types.ModuleType("anchorpy")
    anchorpy_mod.Wallet = FakeWallet

    # --- driftpy ----------------------------------------------------------- #
    class FakeDriftClient:
        def __init__(self, connection, wallet, env, account_subscription=None):
            self.connection = connection
            self.wallet = wallet
            self.env = env
            self.account_subscription = account_subscription

        async def subscribe(self):
            return None

        async def unsubscribe(self):
            rec.unsubscribed.append("drift_client")

    class FakeAccountSubscriptionConfig:
        def __init__(self, kind):
            self.kind = kind

    class FakeUserMap:
        def __init__(self, config):
            self.config = config

        async def subscribe(self):
            return None

        async def unsubscribe(self):
            rec.unsubscribed.append("user_map")

    @dataclass
    class FakeUserMapConfig:
        drift_client: object
        subscription_config: object

    @dataclass
    class FakeWebsocketConfig:
        pass

    class FakeDLOBSubscriber:
        def __init__(self, config):
            self.config = config

        async def subscribe(self):
            return None

        def unsubscribe(self):  # sync, like the real driftpy implementation
            rec.unsubscribed.append("dlob")

        def get_l2_orderbook_sync(self, market, depth=10):
            return None

    @dataclass
    class FakeDLOBClientConfig:
        drift_client: object
        dlob_source: object
        slot_source: object
        update_frequency: float

    class FakeSlotSubscriber:
        def __init__(self, drift_client):
            self.drift_client = drift_client

        async def subscribe(self):
            return None

        async def unsubscribe(self):
            rec.unsubscribed.append("slot_subscriber")

    driftpy_mod = types.ModuleType("driftpy")
    driftpy_drift_client_mod = types.ModuleType("driftpy.drift_client")
    driftpy_drift_client_mod.DriftClient = FakeDriftClient
    driftpy_account_sub_mod = types.ModuleType("driftpy.account_subscription_config")
    driftpy_account_sub_mod.AccountSubscriptionConfig = FakeAccountSubscriptionConfig

    driftpy_user_map_mod = types.ModuleType("driftpy.user_map")
    driftpy_user_map_user_map_mod = types.ModuleType("driftpy.user_map.user_map")
    driftpy_user_map_user_map_mod.UserMap = FakeUserMap
    driftpy_user_map_config_mod = types.ModuleType("driftpy.user_map.user_map_config")
    driftpy_user_map_config_mod.UserMapConfig = FakeUserMapConfig
    driftpy_user_map_config_mod.WebsocketConfig = FakeWebsocketConfig

    driftpy_dlob_mod = types.ModuleType("driftpy.dlob")
    driftpy_dlob_subscriber_mod = types.ModuleType("driftpy.dlob.dlob_subscriber")
    driftpy_dlob_subscriber_mod.DLOBSubscriber = FakeDLOBSubscriber
    driftpy_dlob_client_types_mod = types.ModuleType("driftpy.dlob.client_types")
    driftpy_dlob_client_types_mod.DLOBClientConfig = FakeDLOBClientConfig

    driftpy_slot_mod = types.ModuleType("driftpy.slot")
    driftpy_slot_subscriber_mod = types.ModuleType("driftpy.slot.slot_subscriber")
    driftpy_slot_subscriber_mod.SlotSubscriber = FakeSlotSubscriber

    # Wrap DLOBClientConfig construction so the test can see exactly what was
    # passed, including the (positional) update_frequency argument.
    real_init = FakeDLOBClientConfig.__init__

    def _recording_init(self, drift_client, dlob_source, slot_source, update_frequency):
        rec.dlob_config_args = (drift_client, dlob_source, slot_source, update_frequency)
        real_init(self, drift_client, dlob_source, slot_source, update_frequency)

    FakeDLOBClientConfig.__init__ = _recording_init

    modules = {
        "solana": solana_mod,
        "solana.rpc": solana_rpc_mod,
        "solana.rpc.async_api": solana_rpc_async_api_mod,
        "solders": solders_mod,
        "solders.keypair": solders_keypair_mod,
        "anchorpy": anchorpy_mod,
        "driftpy": driftpy_mod,
        "driftpy.drift_client": driftpy_drift_client_mod,
        "driftpy.account_subscription_config": driftpy_account_sub_mod,
        "driftpy.user_map": driftpy_user_map_mod,
        "driftpy.user_map.user_map": driftpy_user_map_user_map_mod,
        "driftpy.user_map.user_map_config": driftpy_user_map_config_mod,
        "driftpy.dlob": driftpy_dlob_mod,
        "driftpy.dlob.dlob_subscriber": driftpy_dlob_subscriber_mod,
        "driftpy.dlob.client_types": driftpy_dlob_client_types_mod,
        "driftpy.slot": driftpy_slot_mod,
        "driftpy.slot.slot_subscriber": driftpy_slot_subscriber_mod,
    }
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)

    return rec


@pytest.mark.asyncio
async def test_build_converts_update_frequency_ms_to_seconds(driftpy_stubs):
    from src.collector.drift_client import DriftStack

    settings = _Settings(update_frequency_ms=1000)
    stack = await DriftStack.build(settings)

    assert driftpy_stubs.dlob_config_args is not None
    update_frequency = driftpy_stubs.dlob_config_args[3]
    # Must be seconds (~1.0), never the raw millisecond value (1000).
    assert update_frequency == pytest.approx(1.0)

    await stack.close()


@pytest.mark.asyncio
async def test_close_unsubscribes_user_map_and_slot_subscriber(driftpy_stubs):
    from src.collector.drift_client import DriftStack

    settings = _Settings(update_frequency_ms=500)
    stack = await DriftStack.build(settings)
    await stack.close()

    assert set(driftpy_stubs.unsubscribed) == {
        "dlob", "user_map", "slot_subscriber", "drift_client",
    }
    assert driftpy_stubs.connection_closed is True
