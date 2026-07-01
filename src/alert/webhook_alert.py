"""
src/alert/webhook_alert.py

Webhook alert sink for Telegram or Discord (or a generic JSON webhook).

PLUMBING ONLY — the credentials/links are the user's part (see docs/NOTES.md):

* Telegram: set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
* Discord / generic: set ALERT_WEBHOOK_URL

If nothing is configured, :func:`is_configured` is False and the sink is not
added to the dispatcher, so this code never runs by accident. Delivery uses the
stdlib (urllib) — no extra dependency.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from src.alert.base import Alert

logger = logging.getLogger(__name__)

# A shared, bounded pool for ALL webhook deliveries (module-level, not per
# instance): a burst of alerts queues onto these workers instead of spawning
# one OS thread per alert, which under sustained bursts (or a slow/unreachable
# endpoint) would otherwise grow unbounded and could exhaust process/thread
# limits. Queued work that can't be drained in time is dropped (with a
# rate-limited log) rather than piling up indefinitely.
_MAX_WORKERS = 4
_MAX_QUEUED = 200
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="webhook-alert")
_RETRY_DELAYS_SEC = (0.5, 2.0)  # short retry/backoff for transient failures


class _QueueDepthGuard:
    """Tracks in-flight + queued work so we can refuse instead of growing forever."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._pending = 0

    def try_acquire(self) -> bool:
        if self._pending >= self._limit:
            return False
        self._pending += 1
        return True

    def release(self) -> None:
        self._pending -= 1


_queue_guard = _QueueDepthGuard(_MAX_QUEUED)


class WebhookAlert(Alert):
    name = "webhook"

    @staticmethod
    def is_configured(settings) -> bool:
        has_telegram = bool(
            getattr(settings, "telegram_bot_token", "")
            and getattr(settings, "telegram_chat_id", "")
        )
        has_url = bool(getattr(settings, "alert_webhook_url", ""))
        return has_telegram or has_url

    def deliver(self, detection) -> None:
        """Submit HTTP delivery to a bounded worker pool (never blocks the caller)."""
        payload, url = self._build_request(detection)
        if not url:
            return
        if not _queue_guard.try_acquire():
            logger.warning(
                "Webhook queue full (%d pending) — dropping alert for %s",
                _MAX_QUEUED, self._target(),
            )
            return
        future = _executor.submit(self._send, payload, url)
        future.add_done_callback(lambda _f: _queue_guard.release())

    def _send(self, payload: dict, url: str) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        attempts = len(_RETRY_DELAYS_SEC) + 1
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    resp.read()
                return
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == attempts - 1:
                    logger.warning("Webhook delivery failed (%s): %s", self._target(), exc)
                    return
            except Exception as exc:
                if attempt == attempts - 1:
                    logger.warning("Webhook delivery failed (%s): %s", self._target(), exc)
                    return
            time.sleep(_RETRY_DELAYS_SEC[attempt])

    # --- formatting per target ------------------------------------------- #
    def _text(self, d) -> str:
        return f"🔭 [{d.market}] {d.detector} (score {d.score:.2f}) — {d.message}"

    def _target(self) -> str:
        if getattr(self.settings, "telegram_bot_token", ""):
            return "telegram"
        return "webhook"

    def _build_request(self, d):
        token = getattr(self.settings, "telegram_bot_token", "")
        chat_id = getattr(self.settings, "telegram_chat_id", "")
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            return {"chat_id": chat_id, "text": self._text(d)}, url

        url = getattr(self.settings, "alert_webhook_url", "")
        if url:
            # Discord uses {"content": ...}; most generic webhooks accept it too.
            return {"content": self._text(d)}, url

        return None, None
