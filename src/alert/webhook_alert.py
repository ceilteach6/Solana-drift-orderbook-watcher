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

from src.alert.base import Alert

logger = logging.getLogger(__name__)


class WebhookAlert(Alert):
    name = "webhook"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        # Bound the number of concurrent delivery threads. Without this, a
        # burst of detections (the exact condition this tool watches for)
        # can spawn one OS thread per detection with no backpressure.
        max_concurrency = max(1, getattr(settings, "alert_webhook_max_concurrency", 8))
        self._inflight = threading.BoundedSemaphore(max_concurrency)

    @staticmethod
    def is_configured(settings) -> bool:
        has_telegram = bool(
            getattr(settings, "telegram_bot_token", "")
            and getattr(settings, "telegram_chat_id", "")
        )
        has_url = bool(getattr(settings, "alert_webhook_url", ""))
        return has_telegram or has_url

    def deliver(self, detection) -> None:
        """Fire HTTP delivery in a daemon thread to avoid blocking the event loop.

        Concurrent deliveries are capped (``ALERT_WEBHOOK_MAX_CONCURRENCY``);
        once the cap is hit, new deliveries are dropped (and logged) rather
        than queued, since a webhook alert delivered minutes late during a
        thread backlog is not useful.
        """
        payload, url = self._build_request(detection)
        if not url:
            return
        if not self._inflight.acquire(blocking=False):
            logger.warning(
                "Webhook delivery dropped (%d already in flight) for %s",
                self.settings.alert_webhook_max_concurrency, self._target(),
            )
            return
        threading.Thread(
            target=self._send_guarded, args=(payload, url), daemon=True
        ).start()

    def _send_guarded(self, payload: dict, url: str) -> None:
        try:
            self._send(payload, url)
        finally:
            self._inflight.release()

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
