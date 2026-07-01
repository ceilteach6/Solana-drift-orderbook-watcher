"""
tests/test_webhook_alert.py

Regression test for the consecutive-failure escalation in WebhookAlert: a
broken webhook (bad token, revoked URL, sustained rate-limiting) previously
failed silently at WARNING level forever. It must now escalate once, loudly,
after a run of consecutive failures -- and reset if delivery recovers.
"""

import logging
from types import SimpleNamespace
from unittest.mock import patch

from src.alert.webhook_alert import _CONSECUTIVE_FAILURE_ALERT_THRESHOLD, WebhookAlert
from src.detector.base import Detection


def make_sink():
    settings = SimpleNamespace(
        telegram_bot_token="",
        telegram_chat_id="",
        alert_webhook_url="https://example.invalid/webhook",
    )
    return WebhookAlert(settings)


def det():
    return Detection(detector="imbalance", market="SOL-PERP", score=0.9,
                      message="x", details={})


def test_no_escalation_below_threshold(caplog):
    sink = make_sink()
    with patch("src.alert.webhook_alert.urllib.request.urlopen", side_effect=OSError("down")):
        with caplog.at_level(logging.ERROR):
            for _ in range(_CONSECUTIVE_FAILURE_ALERT_THRESHOLD - 1):
                sink._send(*sink._build_request(det()))
    assert not any(r.levelno == logging.ERROR for r in caplog.records)


def test_escalates_once_at_threshold_then_stays_quiet(caplog):
    sink = make_sink()
    with patch("src.alert.webhook_alert.urllib.request.urlopen", side_effect=OSError("down")):
        with caplog.at_level(logging.ERROR):
            for _ in range(_CONSECUTIVE_FAILURE_ALERT_THRESHOLD + 3):
                sink._send(*sink._build_request(det()))
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1


def test_recovery_resets_escalation(caplog):
    sink = make_sink()
    with patch("src.alert.webhook_alert.urllib.request.urlopen", side_effect=OSError("down")):
        for _ in range(_CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
            sink._send(*sink._build_request(det()))

    with patch("src.alert.webhook_alert.urllib.request.urlopen") as ok:
        ok.return_value.__enter__.return_value.read.return_value = b"{}"
        sink._send(*sink._build_request(det()))  # a successful delivery

    caplog.clear()
    with patch("src.alert.webhook_alert.urllib.request.urlopen", side_effect=OSError("down")):
        with caplog.at_level(logging.ERROR):
            for _ in range(_CONSECUTIVE_FAILURE_ALERT_THRESHOLD):
                sink._send(*sink._build_request(det()))
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1  # escalates again after recovering and failing anew
