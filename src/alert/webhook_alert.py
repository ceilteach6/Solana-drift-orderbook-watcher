"""
src/alert/webhook_alert.py

Webhook alert sink for Telegram, Discord, or any generic JSON webhook.

PLUMBING ONLY — the credentials/links are the user's responsibility (see
docs/NOTES.md):

* Telegram : set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env
* Discord / generic : set ALERT_WEBHOOK_URL in .env

If nothing is configured, :meth:`is_configured` returns False and the sink is
not added to the dispatcher, so this code never runs by accident.  Delivery
uses the stdlib ``urllib`` — no extra dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request

from src.alert.base import Alert

logger = logging.getLogger(__name__)


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

    async def deliver(self, detection) -> None:
        payload, url = self._build_request(detection)
        if not url:
            return
        data = json.dumps(payload).encode("utf-8")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._send, data, url)

    def _send(self, data: bytes, url: str) -> None:
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                resp.read()
        except Exception as exc:
            logger.warning(
                "Webhook delivery failed (%s): %s", self._target(), exc, exc_info=True
            )

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
            return {"content": self._text(d)}, url

        return None, None
