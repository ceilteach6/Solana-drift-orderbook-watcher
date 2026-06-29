"""Alerting layer."""

from src.alert.base import Alert, AlertDispatcher
from src.alert.console_alert import ConsoleAlert
from src.alert.webhook_alert import WebhookAlert

__all__ = [
    "Alert",
    "AlertDispatcher",
    "ConsoleAlert",
    "WebhookAlert",
    "build_alert_sinks",
]


def build_alert_sinks(settings) -> list[Alert]:
    """Assemble the active alert sinks for ``settings``.

    Console is always on. The webhook sink is added only when credentials/links
    are present (the user's part — see docs/NOTES.md).
    """
    sinks: list[Alert] = [ConsoleAlert(settings)]
    if WebhookAlert.is_configured(settings):
        sinks.append(WebhookAlert(settings))
    return sinks
