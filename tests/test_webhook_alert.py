"""
tests/test_webhook_alert.py

Tests the webhook alert sink's HTTP delivery. Network-free: ``urlopen`` is
patched with a fake that records calls. Also verifies deliveries run on the
shared bounded thread pool rather than spawning one OS thread per call.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from src.alert.webhook_alert import _MAX_WORKERS, WebhookAlert
from src.detector.base import Detection


def make_settings(**overrides):
    base = dict(
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="https://example.invalid/webhook",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def det(detector="imbalance", score=0.9, market="SOL-PERP"):
    return Detection(detector=detector, market=market, score=score, message="x")


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"ok"


def test_deliver_posts_payload_and_completes():
    sink = WebhookAlert(make_settings())
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, timeout))
        return _FakeResponse()

    with patch("src.alert.webhook_alert.urllib.request.urlopen", fake_urlopen):
        sink.deliver(det())
        # Delivery is async on the shared pool; give it a moment to run.
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)

    assert calls == [("https://example.invalid/webhook", 5)]


def test_unconfigured_target_is_a_noop():
    sink = WebhookAlert(make_settings(alert_webhook_url=""))
    with patch("src.alert.webhook_alert.urllib.request.urlopen") as mock_urlopen:
        sink.deliver(det())
        time.sleep(0.05)
    mock_urlopen.assert_not_called()


def test_bursts_do_not_spawn_unbounded_threads():
    # Before the fix this spawned one daemon Thread per deliver() call, with
    # no cap — a slow endpoint would accumulate threads without bound. It
    # must now stay within the shared pool's fixed worker count.
    sink = WebhookAlert(make_settings())
    release = threading.Event()
    completed = 0
    lock = threading.Lock()

    def slow_urlopen(req, timeout=None):
        nonlocal completed
        release.wait(timeout=2)
        with lock:
            completed += 1
        return _FakeResponse()

    submitted = 50
    threads_before = threading.active_count()
    with patch("src.alert.webhook_alert.urllib.request.urlopen", slow_urlopen):
        for _ in range(submitted):
            sink.deliver(det())
        time.sleep(0.2)
        threads_during = threading.active_count()
        release.set()

        # Drain every queued job while the patch is still active, so none of
        # them fall through to a real network call after this block exits.
        deadline = time.monotonic() + 5
        while completed < submitted and time.monotonic() < deadline:
            time.sleep(0.01)

    assert completed == submitted
    assert threads_during - threads_before <= _MAX_WORKERS
