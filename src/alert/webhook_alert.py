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


class _DaemonWorkerPool:
    """A fixed pool of daemon worker threads consuming from a queue.

    Unlike ``concurrent.futures.ThreadPoolExecutor``, whose workers are
    non-daemon and get joined by an ``atexit`` hook before the interpreter
    can exit, these workers are daemons: a slow in-flight webhook call
    (``_send`` blocks up to its 5s timeout) never delays process shutdown.
    The fixed-size pool plus an unbounded in-memory queue still bounds
    concurrent network calls during an alert storm — extra work just queues
    (cheap) instead of spawning one OS thread per alert (not cheap).
    """

    def __init__(self, max_workers: int, thread_name_prefix: str) -> None:
        self._queue: queue.Queue = queue.Queue()
        for i in range(max_workers):
            threading.Thread(
                target=self._run, daemon=True, name=f"{thread_name_prefix}-{i}"
            ).start()

    def _run(self) -> None:
        while True:
            fn, args = self._queue.get()
            try:
                fn(*args)
            except Exception:
                logger.exception("Webhook worker task failed")
            finally:
                self._queue.task_done()

    def submit(self, fn, *args) -> None:
        self._queue.put((fn, args))

    def drain(self, timeout: float) -> bool:
        """Best-effort wait (bounded by ``timeout``) for every queued/
        in-flight task to finish. Returns True if the queue fully drained,
        False on timeout — workers stay daemons either way, so a delivery
        stuck past the timeout still can't block process exit; this only
        gives a *planned* shutdown (SIGINT/SIGTERM) a chance to flush
        whatever was already queued."""
        if self._queue.unfinished_tasks == 0:
            return True
        done = threading.Event()
        threading.Thread(
            target=lambda: (self._queue.join(), done.set()), daemon=True
        ).start()
        return done.wait(timeout)


# Shared across all WebhookAlert instances: bounds concurrent deliveries so an
# alert storm (e.g. a noisy market tripping many detectors at once) queues
# requests instead of spawning one OS thread per alert.
_EXECUTOR = _DaemonWorkerPool(max_workers=4, thread_name_prefix="webhook-alert")


class WebhookAlert(Alert):
    name = "webhook"

    # Bound on how long a graceful shutdown waits for queued deliveries to
    # flush. Deliberately short and fixed (not user-configurable): it only
    # needs to cover the common case (a handful of queued HTTP calls well
    # under their own 5s timeout), not turn shutdown into an open-ended wait.
    _SHUTDOWN_DRAIN_SEC = 3.0

    @staticmethod
    def is_configured(settings) -> bool:
        has_telegram = bool(
            getattr(settings, "telegram_bot_token", "")
            and getattr(settings, "telegram_chat_id", "")
        )
        has_url = bool(getattr(settings, "alert_webhook_url", ""))
        return has_telegram or has_url

    def deliver(self, detection) -> None:
        """Queue HTTP delivery on the shared executor to avoid blocking the
        event loop (and to bound concurrency during an alert storm)."""
        payload, url = self._build_request(detection)
        if not url:
            return
        _EXECUTOR.submit(self._send, payload, url)

    def close(self) -> None:
        """Give the shared queue a bounded window to flush any deliveries
        still queued/in-flight when the watcher shuts down gracefully,
        instead of silently dropping them the instant the daemon workers
        get torn down with the interpreter."""
        if not _EXECUTOR.drain(self._SHUTDOWN_DRAIN_SEC):
            logger.warning(
                "Webhook queue did not fully drain within %.1fs at shutdown; "
                "some queued alerts may not have been delivered.",
                self._SHUTDOWN_DRAIN_SEC,
            )

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
