"""
tests/test_alert.py

Regression tests for AlertDispatcher.close() and the webhook sink's bounded
shutdown drain: a graceful stop (SIGINT/SIGTERM) should give queued webhook
deliveries a chance to flush instead of silently dropping them the instant
the daemon worker threads are torn down with the interpreter.
"""

from dataclasses import dataclass

from src.alert.base import Alert, AlertDispatcher
from src.alert.webhook_alert import WebhookAlert, _EXECUTOR


@dataclass
class _Settings:
    alert_webhook_url: str = "https://example.invalid/hook"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


class _RecordingSink(Alert):
    name = "recording"

    def __init__(self, settings):
        super().__init__(settings)
        self.closed = False

    def deliver(self, detection) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _BrokenCloseSink(Alert):
    name = "broken"

    def deliver(self, detection) -> None:
        pass

    def close(self) -> None:
        raise RuntimeError("boom")


def test_dispatcher_close_calls_every_sink():
    a = _RecordingSink(_Settings())
    b = _RecordingSink(_Settings())
    dispatcher = AlertDispatcher(_Settings(), [a, b])
    dispatcher.close()
    assert a.closed and b.closed


def test_dispatcher_close_is_resilient_to_a_failing_sink():
    broken = _BrokenCloseSink(_Settings())
    ok = _RecordingSink(_Settings())
    dispatcher = AlertDispatcher(_Settings(), [broken, ok])
    dispatcher.close()  # must not raise
    assert ok.closed


def test_webhook_close_waits_for_queued_delivery_to_finish(monkeypatch):
    started = []
    finished = []

    def fake_send(self, payload, url):
        started.append(1)
        finished.append(1)

    monkeypatch.setattr(WebhookAlert, "_send", fake_send)

    sink = WebhookAlert(_Settings())
    sink.deliver(_Detection())

    sink.close()  # should block until the queued _send above has run

    assert started == [1]
    assert finished == [1]


def test_webhook_drain_returns_true_when_queue_already_empty():
    assert _EXECUTOR.drain(timeout=1.0) is True


@dataclass
class _Detection:
    market: str = "SOL-PERP"
    detector: str = "imbalance"
    score: float = 0.9
    message: str = "test"
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}
