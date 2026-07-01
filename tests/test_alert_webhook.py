"""
tests/test_alert_webhook.py

Regression coverage for webhook delivery concurrency: a burst of alerts must
be served by a bounded worker pool, not one OS thread per alert, since a
real correlated event (multiple detectors/markets tripping at once) is
exactly when an unbounded thread-per-alert design would misbehave most.
"""

import threading
import time
from types import SimpleNamespace

import src.alert.webhook_alert as webhook_alert
from src.detector.base import Detection


def make_settings():
    return SimpleNamespace(
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="http://example.invalid/webhook",
    )


def make_detection():
    return Detection(
        detector="imbalance", market="SOL-PERP", score=0.9, message="x", details={}
    )


def test_webhook_delivery_uses_bounded_pool_not_thread_per_alert(monkeypatch):
    lock = threading.Lock()
    state = {"current": 0, "max_seen": 0, "total": 0}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(req, timeout=5):
        with lock:
            state["current"] += 1
            state["max_seen"] = max(state["max_seen"], state["current"])
        time.sleep(0.1)
        with lock:
            state["current"] -= 1
            state["total"] += 1
        return _FakeResponse()

    monkeypatch.setattr(webhook_alert.urllib.request, "urlopen", fake_urlopen)

    alert = webhook_alert.WebhookAlert(make_settings())
    n_alerts = 3 * webhook_alert._MAX_WORKERS
    for _ in range(n_alerts):
        alert.deliver(make_detection())

    deadline = time.monotonic() + 5
    while state["total"] < n_alerts and time.monotonic() < deadline:
        time.sleep(0.05)

    assert state["total"] == n_alerts
    assert state["max_seen"] <= webhook_alert._MAX_WORKERS
