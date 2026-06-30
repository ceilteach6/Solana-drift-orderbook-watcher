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
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from src.alert.base import Alert

logger = logging.getLogger(__name__)


class WebhookAlert(Alert):
    name = "webhook"

    # Shared, bounded pool for all instances — a raw ``threading.Thread`` per
    # delivery would let a sustained burst of alerts spawn unbounded OS
    # threads against a slow/unreachable endpoint. Built lazily (and once)
    # since most runs never configure a webhook at all.
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @staticmethod
    def is_configured(settings) -> bool:
        has_telegram = bool(
            getattr(settings, "telegram_bot_token", "")
            and getattr(settings, "telegram_chat_id", "")
        )
        has_url = bool(getattr(settings, "alert_webhook_url", ""))
        return has_telegram or has_url

    @classmethod
    def _get_executor(cls, max_workers: int) -> ThreadPoolExecutor:
        if cls._executor is None:
            with cls._executor_lock:
                if cls._executor is None:
                    cls._executor = ThreadPoolExecutor(
                        max_workers=max(1, max_workers),
                        thread_name_prefix="webhook-alert",
                    )
        return cls._executor

    def deliver(self, detection) -> None:
        """Queue HTTP delivery on a bounded worker pool to avoid blocking the
        event loop while capping concurrent in-flight deliveries."""
        payload, url = self._build_request(detection)
        if not url:
            return
        max_workers = getattr(self.settings, "webhook_max_workers", 4)
        self._get_executor(max_workers).submit(self._send, payload, url)

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
