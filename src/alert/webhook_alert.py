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
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from src.alert.base import Alert

logger = logging.getLogger(__name__)

# Bounds concurrent webhook deliveries so an alert storm (many detectors
# firing across many markets in one tick) can't spawn unbounded threads —
# excess deliveries queue instead of piling up as raw OS threads.
_DELIVERY_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook-alert")

# ThreadPoolExecutor.submit() never blocks or rejects — its internal work
# queue is unbounded. During a sustained outage (bad URL, target down, DNS
# hang near the request timeout) the 8 workers drain at most ~8 requests per
# timeout window, but every tick keeps calling deliver(), so the backlog
# behind them would otherwise grow for as long as the process runs. This
# semaphore caps how many deliveries can be queued-or-in-flight at once;
# anything beyond that is dropped (rate-limited log) instead of queued, so a
# webhook outage degrades gracefully instead of leaking memory.
_MAX_QUEUED = 64
_queue_slots = threading.Semaphore(_MAX_QUEUED)
_last_overflow_log = 0.0
_OVERFLOW_LOG_INTERVAL_SEC = 30.0


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
        """Queue HTTP delivery on a bounded pool to avoid blocking the event loop."""
        payload, url = self._build_request(detection)
        if not url:
            return
        if not _queue_slots.acquire(blocking=False):
            self._log_overflow()
            return
        try:
            _DELIVERY_POOL.submit(self._send, payload, url)
        except Exception:
            _queue_slots.release()
            raise

    @staticmethod
    def _log_overflow() -> None:
        global _last_overflow_log
        now = time.monotonic()
        if now - _last_overflow_log >= _OVERFLOW_LOG_INTERVAL_SEC:
            _last_overflow_log = now
            logger.warning(
                "Webhook delivery backlog full (%d queued/in-flight) — dropping "
                "alert; target may be down or slow.", _MAX_QUEUED,
            )

    def _send(self, payload: dict, url: str) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    resp.read()
            except Exception as exc:
                logger.warning("Webhook delivery failed (%s): %s", self._target(), exc)
        finally:
            _queue_slots.release()

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
