"""
tests/test_alert.py

Tests the webhook alert sink's concurrency bound: a burst of detections must
not be able to spawn unbounded OS threads (see src/alert/webhook_alert.py).
"""

import threading
import time
from types import SimpleNamespace

from src.alert.webhook_alert import WebhookAlert
from src.detector.base import Detection


def make_settings(**overrides):
    base = dict(
        alert_webhook_url="https://example.invalid/webhook",
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_max_concurrency=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def det(market="SOL-PERP"):
    return Detection(detector="flicker", market=market, score=0.9, message="x")


def test_is_configured_true_for_generic_webhook():
    assert WebhookAlert.is_configured(make_settings()) is True


def test_is_configured_false_when_nothing_set():
    settings = make_settings(alert_webhook_url="")
    assert WebhookAlert.is_configured(settings) is False


def test_deliver_drops_beyond_max_concurrency(monkeypatch):
    """Bursting past the cap must drop excess deliveries instead of spawning
    unbounded threads — it must not block the calling (event loop) thread."""
    settings = make_settings(alert_webhook_max_concurrency=2)
    sink = WebhookAlert(settings)

    release = threading.Event()
    started = threading.Semaphore(0)

    def fake_send(self, payload, url):
        started.release()
        release.wait(timeout=5)

    monkeypatch.setattr(WebhookAlert, "_send", fake_send)

    # First two deliveries should each get their own in-flight thread.
    sink.deliver(det())
    sink.deliver(det())
    assert started.acquire(timeout=2)
    assert started.acquire(timeout=2)

    # The third, while the first two are still "in flight", must be dropped
    # immediately rather than spawning a third thread or blocking.
    start = time.monotonic()
    sink.deliver(det())
    elapsed = time.monotonic() - start
    assert elapsed < 1.0  # dropped, not blocked waiting on the semaphore

    release.set()


def test_deliver_allows_new_request_after_one_completes(monkeypatch):
    settings = make_settings(alert_webhook_max_concurrency=1)
    sink = WebhookAlert(settings)

    calls = []
    done = threading.Event()

    def fake_send(self, payload, url):
        calls.append(url)
        done.set()

    monkeypatch.setattr(WebhookAlert, "_send", fake_send)

    sink.deliver(det())
    assert done.wait(timeout=2)
    done.clear()

    sink.deliver(det())
    assert done.wait(timeout=2)

    assert len(calls) == 2
