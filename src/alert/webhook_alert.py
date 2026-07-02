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
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait as wait_futures

from src.alert.base import Alert

logger = logging.getLogger(__name__)

# Bounds concurrent webhook deliveries so an alert storm (many detectors
# firing across many markets in one tick) can't spawn unbounded threads —
# excess deliveries queue instead of piling up as raw OS threads.
_DELIVERY_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="webhook-alert")

# deliver() is fire-and-forget: it submits to _DELIVERY_POOL and returns
# immediately, without waiting for the HTTP call. main.py exits via
# os._exit() (see its module docstring) specifically to dodge
# concurrent.futures.thread's atexit hook that would otherwise join every
# executor thread ever created — but os._exit() skips *all* interpreter
# shutdown machinery, including any alert still queued or mid-retry-sleep on
# this pool. Without tracking outstanding futures and giving them a bounded
# chance to finish before that exit, an alert fired on the process's last
# tick (a shutdown-triggering error, a Ctrl+C, or RUN_DURATION_SEC ending)
# would be silently discarded with no log line. See drain().
_pending_lock = threading.Lock()
_pending_futures: set = set()


def _track(future) -> None:
    with _pending_lock:
        _pending_futures.add(future)
    future.add_done_callback(_untrack)


def _untrack(future) -> None:
    with _pending_lock:
        _pending_futures.discard(future)


def drain(timeout: float = 5.0) -> None:
    """Give queued/in-flight webhook deliveries a bounded chance to finish.

    Call this during shutdown, before anything that skips normal interpreter
    cleanup (e.g. ``os._exit()``). Bounded rather than an unconditional
    ``_DELIVERY_POOL.shutdown(wait=True)``: a delivery genuinely stuck past
    its own socket timeout must not turn a fast shutdown back into the kind
    of hang ``os._exit()`` was introduced to avoid.
    """
    with _pending_lock:
        futures = list(_pending_futures)
    if not futures:
        return
    _, not_done = wait_futures(futures, timeout=timeout)
    if not_done:
        logger.warning(
            "Shutdown: %d webhook delivery(ies) still in flight after %.0fs — dropped",
            len(not_done), timeout,
        )

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
            _track(_DELIVERY_POOL.submit(self._send, target, payload, url))

    def close(self, timeout: float = 5.0) -> None:
        drain(timeout=timeout)

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
