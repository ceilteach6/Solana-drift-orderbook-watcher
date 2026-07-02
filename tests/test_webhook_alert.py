"""
tests/test_webhook_alert.py

Telegram and the generic webhook (Discord/etc) are independent, optional
channels. A user who configures both must get delivery to both, not have one
silently shadow the other.
"""

from types import SimpleNamespace

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
