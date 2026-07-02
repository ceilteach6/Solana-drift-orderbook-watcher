"""
tests/test_webhook_alert.py

Telegram and the generic webhook (Discord/etc) are independent, optional
channels. A user who configures both must get delivery to both, not have one
silently shadow the other.
"""

import threading
import time
import urllib.error
from types import SimpleNamespace

from src.alert import webhook_alert as webhook_alert_module
from src.alert.webhook_alert import WebhookAlert
from src.detector.base import Detection


def _detection():
    return Detection(detector="flicker", market="SOL-PERP", score=0.9, message="test")


def _settings(**overrides):
    base = dict(
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_telegram_only():
    alert = WebhookAlert(_settings(telegram_bot_token="tok", telegram_chat_id="123"))
    requests = alert._build_requests(_detection())
    targets = [t for t, _, _ in requests]
    assert targets == ["telegram"]


def test_webhook_only():
    alert = WebhookAlert(_settings(alert_webhook_url="https://discord.example/hook"))
    requests = alert._build_requests(_detection())
    targets = [t for t, _, _ in requests]
    assert targets == ["webhook"]


def test_both_configured_deliver_to_both():
    alert = WebhookAlert(
        _settings(
            telegram_bot_token="tok",
            telegram_chat_id="123",
            alert_webhook_url="https://discord.example/hook",
        )
    )
    requests = alert._build_requests(_detection())
    targets = sorted(t for t, _, _ in requests)
    assert targets == ["telegram", "webhook"]

    urls = {t: u for t, _, u in requests}
    assert "telegram.org" in urls["telegram"]
    assert urls["webhook"] == "https://discord.example/hook"


def test_neither_configured_produces_nothing():
    alert = WebhookAlert(_settings())
    assert alert._build_requests(_detection()) == []


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self):
        return b"{}"


def test_send_retries_a_transient_failure_then_succeeds(monkeypatch):
    # A network blip or brief rate-limit shouldn't permanently drop an
    # alert that a second attempt would have delivered fine.
    monkeypatch.setattr(webhook_alert_module.time, "sleep", lambda s: None)
    calls = []

    def fake_urlopen(req, timeout=5):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return _FakeResponse()

    monkeypatch.setattr(webhook_alert_module.urllib.request, "urlopen", fake_urlopen)

    alert = WebhookAlert(_settings())
    alert._send("webhook", {"content": "hi"}, "https://example.invalid/hook")  # must not raise

    assert len(calls) == 3


def test_send_does_not_retry_a_non_retryable_http_error(monkeypatch):
    # A 401/404/etc would fail identically on every retry — retrying just
    # delays the (already final) failure log.
    monkeypatch.setattr(webhook_alert_module.time, "sleep", lambda s: None)
    calls = []

    def fake_urlopen(req, timeout=5):
        calls.append(1)
        raise urllib.error.HTTPError(
            "https://example.invalid/hook", 401, "unauthorized", {}, None
        )

    monkeypatch.setattr(webhook_alert_module.urllib.request, "urlopen", fake_urlopen)

    alert = WebhookAlert(_settings())
    alert._send("webhook", {"content": "hi"}, "https://example.invalid/hook")  # must not raise

    assert len(calls) == 1


def test_send_gives_up_after_max_attempts_on_persistent_failures(monkeypatch):
    monkeypatch.setattr(webhook_alert_module.time, "sleep", lambda s: None)
    calls = []

    def fake_urlopen(req, timeout=5):
        calls.append(1)
        raise urllib.error.HTTPError(
            "https://example.invalid/hook", 503, "unavailable", {}, None
        )

    monkeypatch.setattr(webhook_alert_module.urllib.request, "urlopen", fake_urlopen)

    alert = WebhookAlert(_settings())
    alert._send("webhook", {"content": "hi"}, "https://example.invalid/hook")  # must not raise

    assert len(calls) == webhook_alert_module._MAX_SEND_ATTEMPTS


def test_close_waits_for_a_delivery_queued_moments_before_shutdown(monkeypatch):
    # Regression: deliver() is fire-and-forget (submits to a background pool
    # and returns immediately). main.py exits via os._exit(), which skips
    # normal interpreter shutdown entirely — so a detection alerted on the
    # last tick before shutdown was silently dropped unless something waits
    # for the pool. close()/drain() must block until the queued send
    # actually runs, not just until submit() returns.
    sent = threading.Event()

    def fake_urlopen(req, timeout=5):
        time.sleep(0.05)  # simulate real network latency
        sent.set()
        return _FakeResponse()

    monkeypatch.setattr(webhook_alert_module.urllib.request, "urlopen", fake_urlopen)

    alert = WebhookAlert(_settings(alert_webhook_url="https://example.invalid/hook"))
    alert.deliver(_detection())

    assert not sent.is_set()  # deliver() must not itself have blocked
    alert.close(timeout=5.0)
    assert sent.is_set()


def test_close_gives_up_after_timeout_and_reports_still_in_flight(monkeypatch, caplog):
    # A delivery genuinely stuck past its own socket timeout must not turn a
    # bounded drain back into an unbounded hang.
    release = threading.Event()

    def fake_urlopen(req, timeout=5):
        release.wait(2.0)
        return _FakeResponse()

    monkeypatch.setattr(webhook_alert_module.urllib.request, "urlopen", fake_urlopen)

    alert = WebhookAlert(_settings(alert_webhook_url="https://example.invalid/hook"))
    alert.deliver(_detection())

    with caplog.at_level("WARNING", logger=webhook_alert_module.logger.name):
        alert.close(timeout=0.05)
    assert "still in flight" in caplog.text

    release.set()  # let the background send finish so it doesn't leak past the test


def test_drain_is_a_noop_when_nothing_is_pending():
    # Must not block or raise when no webhook sink was ever used (the
    # common case: console-only alerting).
    webhook_alert_module.drain(timeout=1.0)
