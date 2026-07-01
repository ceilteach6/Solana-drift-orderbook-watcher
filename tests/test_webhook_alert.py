"""
tests/test_webhook_alert.py

Regression test: WebhookAlert must not spawn an unbounded number of threads.
Previously every deliver() call spawned a brand-new daemon thread with no cap;
a slow/dead endpoint let threads accumulate faster than they drained. Now
delivery runs on a small fixed-size pool with a bounded in-flight count, and
deliver() drops (with a warning) instead of growing unbounded when the
backlog is full. Network-free (``_send`` is monkeypatched to block on an
Event instead of making a real HTTP request).
"""

import threading
import time
from types import SimpleNamespace

from src.alert.webhook_alert import WebhookAlert
from src.detector.base import Detection


def make_alert():
    settings = SimpleNamespace(alert_webhook_url="http://example.invalid/hook",
                                telegram_bot_token="", telegram_chat_id="")
    return WebhookAlert(settings)


def make_detection():
    return Detection(detector="d", market="SOL-PERP", score=1.0, message="m", details={})


def test_deliver_never_blocks_the_caller(monkeypatch):
    alert = make_alert()
    release = threading.Event()
    monkeypatch.setattr(alert, "_send", lambda payload, url: release.wait(timeout=2))
    try:
        start = time.monotonic()
        alert.deliver(make_detection())
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, "deliver() must submit to the pool, not block on delivery"
    finally:
        release.set()
        alert._executor.shutdown(wait=True)


def test_deliver_drops_instead_of_growing_unbounded_when_backlog_is_full(monkeypatch):
    alert = make_alert()
    alert._MAX_INFLIGHT = 3
    alert._inflight = threading.BoundedSemaphore(3)
    release = threading.Event()
    monkeypatch.setattr(alert, "_send", lambda payload, url: release.wait(timeout=2))
    try:
        # Saturate the backlog.
        for _ in range(3):
            alert.deliver(make_detection())
        time.sleep(0.05)  # let the pool pick up the submitted work
        # One more must be dropped, not queued indefinitely.
        alert.deliver(make_detection())
        assert alert._inflight.acquire(blocking=False) is False
    finally:
        release.set()
        alert._executor.shutdown(wait=True)
