"""
tests/test_webhook_alert.py

Guards the delivery-backlog bound in WebhookAlert: ThreadPoolExecutor.submit()
never blocks or rejects on its own (its internal queue is unbounded), so the
semaphore gate in webhook_alert.py is what keeps a sustained outage from
growing the backlog forever. Covers both halves: overflow is dropped instead
of queued, and a completed (or failed) send frees its slot back up.
"""

from types import SimpleNamespace
from unittest.mock import patch

from src.alert import webhook_alert as wa
from src.detector.base import Detection


def make_settings(**overrides):
    base = dict(alert_webhook_url="https://example.invalid/hook",
                telegram_bot_token="", telegram_chat_id="")
    base.update(overrides)
    return SimpleNamespace(**base)


def make_detection():
    return Detection(detector="test", market="SOL-PERP", score=0.9, message="spoof")


def test_send_releases_its_slot_even_when_the_request_fails():
    alert = wa.WebhookAlert(make_settings())
    assert wa._queue_slots.acquire(blocking=False)  # simulate the slot deliver() would hold
    with patch("urllib.request.urlopen", side_effect=OSError("boom")):
        alert._send({"content": "x"}, "https://example.invalid/hook")
    # _send's finally must have released it back
    assert wa._queue_slots.acquire(blocking=False)
    wa._queue_slots.release()


def test_deliver_drops_instead_of_queuing_when_backlog_is_full():
    alert = wa.WebhookAlert(make_settings())
    acquired = 0
    try:
        while wa._queue_slots.acquire(blocking=False):
            acquired += 1
        with patch.object(wa._DELIVERY_POOL, "submit") as mock_submit:
            alert.deliver(make_detection())  # no free slot -> must not enqueue
            mock_submit.assert_not_called()
    finally:
        for _ in range(acquired):
            wa._queue_slots.release()


def test_deliver_queues_normally_when_a_slot_is_free():
    alert = wa.WebhookAlert(make_settings())
    with patch.object(wa._DELIVERY_POOL, "submit") as mock_submit:
        alert.deliver(make_detection())
        mock_submit.assert_called_once()
    # deliver() only reserves the slot; submit() is mocked so nothing runs to
    # release it — give it back so later tests see full capacity again.
    wa._queue_slots.release()
