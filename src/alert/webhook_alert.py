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

# Bounds concurrent webhook deliveries so an alert storm (many detectors
# firing across many markets in one tick) can't spawn unbounded threads —
# excess deliveries queue instead of piling up as raw OS threads.
_DELIVERY_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook-alert")

# A spoofing event is exactly the moment many detections fire close together,
# which is also when transient delivery failures (rate limits, brief network
# blips) are most likely — the highest-value alerts are the ones most at risk
# of a one-shot send silently dropping them. Retry a bounded number of times
# on failures that are plausibly transient before giving up on that alert.
_MAX_SEND_ATTEMPTS = 3
_RETRY_BASE_DELAY_SEC = 0.5
_RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


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
        for target, payload, url in self._build_requests(detection):
            _DELIVERY_POOL.submit(self._send, target, payload, url)

    def _send(self, target: str, payload: dict, url: str) -> None:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                    resp.read()
                return
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code not in _RETRYABLE_HTTP_CODES:
                    break  # e.g. 400/401/404 — a retry would fail identically
            except Exception as exc:
                last_exc = exc  # timeout, connection reset, DNS blip, etc.
            if attempt < _MAX_SEND_ATTEMPTS:
                # Runs on the bounded delivery pool thread, not the event
                # loop, so a blocking sleep here can't stall polling/detection.
                time.sleep(_RETRY_BASE_DELAY_SEC * attempt)
        logger.warning(
            "Webhook delivery failed (%s) after %d attempt(s): %s",
            target, _MAX_SEND_ATTEMPTS, last_exc,
        )

    # --- formatting per target ------------------------------------------- #
    def _text(self, d) -> str:
        return f"🔭 [{d.market}] {d.detector} (score {d.score:.2f}) — {d.message}"

    def _build_requests(self, d):
        """Build one (target, payload, url) tuple per configured channel.

        Telegram and the generic webhook are independent channels — a user who
        sets up both (e.g. Telegram + Discord) expects delivery to both, not
        one silently shadowing the other.
        """
        requests = []

        token = getattr(self.settings, "telegram_bot_token", "")
        chat_id = getattr(self.settings, "telegram_chat_id", "")
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            requests.append(("telegram", {"chat_id": chat_id, "text": self._text(d)}, url))

        url = getattr(self.settings, "alert_webhook_url", "")
        if url:
            # Discord uses {"content": ...}; most generic webhooks accept it too.
            requests.append(("webhook", {"content": self._text(d)}, url))

        return requests
