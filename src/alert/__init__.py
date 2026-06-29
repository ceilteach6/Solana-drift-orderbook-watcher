"""Alerting layer."""

from src.alert.base import Alert, AlertDispatcher
from src.alert.console_alert import ConsoleAlert
from src.alert.sqlite_alert import SqliteAlert
from src.alert.webhook_alert import WebhookAlert

__all__ = [
    "Alert",
    "AlertDispatcher",
    "ConsoleAlert",
    "SqliteAlert",
    "WebhookAlert",
    "build_alert_sinks",
]


def build_alert_sinks(settings) -> list[Alert]:
    """Assemble the active alert sinks from ``settings``.

    Console is always on.  Webhook and SQLite sinks are added only when
    their respective credentials/paths are configured (see docs/NOTES.md).
    """
    sinks: list[Alert] = [ConsoleAlert(settings)]
    if WebhookAlert.is_configured(settings):
        sinks.append(WebhookAlert(settings))
    if SqliteAlert.is_configured(settings):
        sinks.append(SqliteAlert(settings))
    return sinks
