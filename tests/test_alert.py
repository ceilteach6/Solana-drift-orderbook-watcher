"""
tests/test_alert.py

AlertDispatcher.close() / Alert.close() wiring, and the WebhookAlert delivery
pool shutdown.

Regression context: the webhook delivery pool (a module-level
ThreadPoolExecutor) was never shut down anywhere, so an alert storm queued
against a slow/unreachable endpoint could hold the process open at exit far
longer than the poll loop itself ran. Watcher.start() now calls
AlertDispatcher.close() -> sink.close() in its finally block, mirroring how
the feed's executor is already torn down.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.alert import webhook_alert as webhook_alert_module
from src.alert.base import Alert, AlertDispatcher
from src.alert.webhook_alert import WebhookAlert


class _FakeSettings:
    telegram_bot_token = ""
    telegram_chat_id = ""
    alert_webhook_url = "http://example.invalid/hook"
    alert_min_score = 0.0


class _RecordingSink(Alert):
    name = "recording"

    def __init__(self, settings):
        super().__init__(settings)
        self.closed = False

    def deliver(self, detection) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _BrokenCloseSink(Alert):
    name = "broken"

    def deliver(self, detection) -> None:
        pass

    def close(self) -> None:
        raise RuntimeError("boom")


def test_dispatcher_close_closes_every_sink():
    settings = _FakeSettings()
    sinks = [_RecordingSink(settings), _RecordingSink(settings)]
    dispatcher = AlertDispatcher(settings, sinks)

    dispatcher.close()

    assert all(sink.closed for sink in sinks)


def test_dispatcher_close_isolates_a_sink_that_raises():
    settings = _FakeSettings()
    good = _RecordingSink(settings)
    dispatcher = AlertDispatcher(settings, [_BrokenCloseSink(settings), good])

    dispatcher.close()  # must not raise despite the broken sink

    assert good.closed


def test_alert_base_close_is_a_noop_by_default():
    Alert(_FakeSettings()).close()  # must not raise


@pytest.fixture
def isolated_delivery_pool(monkeypatch):
    """Swap in a throwaway pool so this test's shutdown() can't break
    other tests sharing the real module-level _DELIVERY_POOL."""
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-webhook-alert")
    monkeypatch.setattr(webhook_alert_module, "_DELIVERY_POOL", pool)
    yield pool
    if not pool._shutdown:
        pool.shutdown(wait=False, cancel_futures=True)


def test_webhook_alert_close_shuts_down_delivery_pool(isolated_delivery_pool):
    sink = WebhookAlert(_FakeSettings())

    sink.close()

    assert isolated_delivery_pool._shutdown is True
    with pytest.raises(RuntimeError):
        isolated_delivery_pool.submit(lambda: None)
