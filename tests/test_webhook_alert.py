"""
tests/test_webhook_alert.py

Guards WebhookAlert against leaking the Telegram bot token (embedded in the
request URL) into logs when delivery fails.
"""

from types import SimpleNamespace

from src.alert.webhook_alert import WebhookAlert


def make_settings(**overrides):
    base = dict(
        telegram_bot_token="secret-token-123",
        telegram_chat_id="chat-1",
        alert_webhook_url="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_redact_strips_telegram_token_from_exception_text():
    alert = WebhookAlert(make_settings())
    raw = "HTTPError fetching https://api.telegram.org/botsecret-token-123/sendMessage"
    assert "secret-token-123" not in alert._redact(raw)


def test_redact_strips_generic_webhook_url_from_exception_text():
    alert = WebhookAlert(
        make_settings(
            telegram_bot_token="",
            telegram_chat_id="",
            alert_webhook_url="https://discord.com/api/webhooks/123/super-secret",
        )
    )
    raw = "URLError: <urlopen error> for https://discord.com/api/webhooks/123/super-secret"
    assert "super-secret" not in alert._redact(raw)


def test_redact_is_noop_when_text_has_no_secret():
    alert = WebhookAlert(make_settings())
    assert alert._redact("connection refused") == "connection refused"
