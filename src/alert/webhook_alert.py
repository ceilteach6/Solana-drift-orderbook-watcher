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

import concurrent.futures
import json
import logging
import threading
import urllib.request

from src.alert.base import Alert

logger = logging.getLogger(__name__)

# Shared, bounded pool for webhook delivery. A burst of alerts queues here
# instead of spawning an unbounded number of OS threads/sockets — the queue
# has no size limit, but concurrency (and therefore in-flight connections) is
# capped.
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="webhook-alert"
)

# After this many consecutive delivery failures (bad token, revoked webhook,
# sustained rate-limiting), a single ERROR-level escalation is logged. Without
# this, a broken webhook fails silently at warning level forever: on an
# unattended run, actionable alerts (e.g. a detected spoof) can stop reaching
# Telegram/Discord for the rest of the run with nothing louder than scrolling
# log lines to notice it by.
_CONSECUTIVE_FAILURE_ALERT_THRESHOLD = 5


class WebhookAlert(Alert):
    name = "webhook"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._escalated = False

    @staticmethod
    def is_configured(settings) -> bool:
        has_telegram = bool(
            getattr(settings, "telegram_bot_token", "")
            and getattr(settings, "telegram_chat_id", "")
        )
        has_url = bool(getattr(settings, "alert_webhook_url", ""))
        return has_telegram or has_url

    def deliver(self, detection) -> None:
        """Queue HTTP delivery on the shared pool to avoid blocking the caller."""
        payload, url = self._build_request(detection)
        if not url:
            return
        _EXECUTOR.submit(self._send, payload, url)

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
            self._on_failure()
        else:
            self._on_success()

    # --- consecutive-failure escalation (runs on the executor threads) --- #
    def _on_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._escalated = False

    def _on_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            count = self._consecutive_failures
            should_escalate = (
                count >= _CONSECUTIVE_FAILURE_ALERT_THRESHOLD and not self._escalated
            )
            if should_escalate:
                self._escalated = True
        if should_escalate:
            logger.error(
                "Webhook delivery (%s) has failed %d times in a row — alerts "
                "are NOT reaching this sink (check the token/URL/network). "
                "Detections are still visible via the console sink.",
                self._target(), count,
            )

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
