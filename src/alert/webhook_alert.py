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
import queue
import threading
import urllib.request

from src.alert.base import Alert

logger = logging.getLogger(__name__)

# Fixed-size pool of daemon worker threads shared by every WebhookAlert
# instance. A raw "one thread per alert" approach has no ceiling: a noisy
# market tripping several detectors across several markets every tick can
# spawn dozens of concurrent threads/sockets per second indefinitely. Workers
# are daemons (unlike a ThreadPoolExecutor's, which are joined at interpreter
# exit) so a slow in-flight delivery never delays shutdown; extra work simply
# queues instead of spawning more OS threads.
_MAX_WORKERS = 4


class _WebhookWorkerPool:
    def __init__(self, max_workers: int) -> None:
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        for i in range(max_workers):
            threading.Thread(
                target=self._run, daemon=True, name=f"webhook-alert-{i}"
            ).start()

    def _run(self) -> None:
        while True:
            fn, args = self._queue.get()
            try:
                fn(*args)
            except Exception:
                logger.exception("Webhook worker task failed")

    def submit(self, fn, *args) -> None:
        self._queue.put((fn, args))


_POOL = _WebhookWorkerPool(_MAX_WORKERS)


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
        """Queue HTTP delivery on the shared worker pool — bounded concurrency,
        never blocks the event loop."""
        payload, url = self._build_request(detection)
        if not url:
            return
        _POOL.submit(self._send, payload, url)

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
