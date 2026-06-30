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

# Bounded worker pool shared by every WebhookAlert instance. A naive
# thread-per-alert design lets a noisy market (many qualifying detections per
# tick, across markets/detectors) or a slow/unreachable downstream endpoint
# spawn unboundedly many OS threads. Workers are daemons (process exit is
# never blocked) and idle out after 30s so a quiet watcher doesn't keep
# threads alive for nothing; they respawn on demand.
_MAX_WORKERS = 4
_MAX_QUEUE = 256


class _DeliveryPool:
    def __init__(self, max_workers: int = _MAX_WORKERS, max_queue: int = _MAX_QUEUE) -> None:
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._max_workers = max_workers
        self._workers: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _ensure_workers(self) -> None:
        with self._lock:
            self._workers = [t for t in self._workers if t.is_alive()]
            while len(self._workers) < self._max_workers:
                t = threading.Thread(target=self._run, daemon=True)
                t.start()
                self._workers.append(t)

    def _run(self) -> None:
        while True:
            try:
                func, args = self._queue.get(timeout=30)
            except queue.Empty:
                return  # idle worker exits; _ensure_workers respawns on demand
            try:
                func(*args)
            except Exception:
                logger.exception("Webhook delivery worker crashed")
            finally:
                self._queue.task_done()

    def submit(self, func, *args) -> None:
        self._ensure_workers()
        try:
            self._queue.put_nowait((func, args))
        except queue.Full:
            logger.warning("Webhook delivery queue full (%d pending); dropping alert", _MAX_QUEUE)


_pool = _DeliveryPool()


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
        """Queue HTTP delivery on the bounded worker pool (non-blocking)."""
        payload, url = self._build_request(detection)
        if not url:
            return
        _pool.submit(self._send, payload, url)

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
