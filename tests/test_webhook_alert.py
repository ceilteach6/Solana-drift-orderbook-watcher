"""
tests/test_webhook_alert.py

Telegram and the generic webhook (Discord/etc) are independent, optional
channels. A user who configures both must get delivery to both, not have one
silently shadow the other.
"""

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
    assert urls["telegram"] == "https://api.telegram.org/bottok/sendMessage"
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
