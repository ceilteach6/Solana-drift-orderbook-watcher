"""
tests/test_webhook_alert.py

Unit tests for WebhookAlert's delivery concurrency bound. Network-free — the
actual HTTP send is monkeypatched out.
"""

import threading
import time
from types import SimpleNamespace

from src.alert.webhook_alert import _MAX_WORKERS, WebhookAlert


def make_settings(**overrides):
    base = dict(
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="https://example.invalid/webhook",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def det(market="SOL-PERP", detector="imbalance", score=0.9, message="x"):
    return SimpleNamespace(market=market, detector=detector, score=score, message=message)


def test_is_configured_requires_a_target():
    assert WebhookAlert.is_configured(make_settings())
    assert not WebhookAlert.is_configured(make_settings(alert_webhook_url=""))


def test_many_concurrent_deliveries_do_not_spawn_unbounded_threads(monkeypatch):
    """A burst of alerts must be served by the fixed worker pool, not one
    thread per alert — thread count must stay bounded regardless of burst
    size."""
    calls = []
    lock = threading.Lock()

    def fake_send(self, payload, url):
        with lock:
            calls.append(url)
        time.sleep(0.01)

    monkeypatch.setattr(WebhookAlert, "_send", fake_send)

    alert = WebhookAlert(make_settings())
    before = threading.active_count()

    n = 50
    for _ in range(n):
        alert.deliver(det())

    deadline = time.monotonic() + 5
    while len(calls) < n and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(calls) == n
    # Thread count grew by at most the fixed pool size, not by n.
    assert threading.active_count() <= before + _MAX_WORKERS


def test_deliver_without_configured_target_does_not_queue_work(monkeypatch):
    sent = []
    monkeypatch.setattr(WebhookAlert, "_send", lambda self, payload, url: sent.append(url))
    alert = WebhookAlert(make_settings(alert_webhook_url=""))
    alert.deliver(det())
    time.sleep(0.05)
    assert sent == []
