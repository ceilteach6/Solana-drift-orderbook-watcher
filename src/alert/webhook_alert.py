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
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from src.alert.base import Alert

logger = logging.getLogger(__name__)


class WebhookAlert(Alert):
    name = "webhook"

    # A bursty market (the exact scenario this tool exists to catch) can
    # produce many qualifying detections in a row. A bounded pool delivers
    # them without blocking the event loop, but — unlike spawning a raw
    # thread per call — caps how many requests are ever in flight at once,
    # so a slow/rate-limiting endpoint (Telegram 429s readily) queues
    # instead of piling up unbounded OS threads.
    _MAX_WORKERS = 4

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._executor = ThreadPoolExecutor(
            max_workers=self._MAX_WORKERS, thread_name_prefix="webhook-alert"
        )

    @staticmethod
    def is_configured(settings) -> bool:
        has_telegram = bool(
            getattr(settings, "telegram_bot_token", "")
            and getattr(settings, "telegram_chat_id", "")
        )
        has_url = bool(getattr(settings, "alert_webhook_url", ""))
        return has_telegram or has_url

    def deliver(self, detection) -> None:
        """Queue HTTP delivery on the bounded pool to avoid blocking the event loop."""
        payload, url = self._build_request(detection)
        if not url:
            return
        self._executor.submit(self._send, payload, url)

    def close(self) -> None:
        """Drop anything still queued and wait (bounded by the 5s per-request
        timeout in :meth:`_send`) for already in-flight deliveries.

        Without this, the executor's non-daemon threads are only ever reaped
        by ``concurrent.futures``' atexit hook, which joins them unbounded —
        a burst of queued alerts right before shutdown would otherwise stall
        process exit instead of failing fast.
        """
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _send(self, payload: dict, url: str) -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                resp.read()
        except Exception as exc:
            logger.warning("Webhook delivery failed (%s): %s", self._target(), exc)

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
