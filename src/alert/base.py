"""
src/alert/base.py

Alert sink interface + a dispatcher that fans a detection out to every
configured sink, after filtering by ``ALERT_MIN_SCORE``.

Add a new channel (e-mail, PagerDuty, ...) by subclassing :class:`Alert` and
registering it in :func:`src.alert.build_alert_sinks`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Alert:
    """Base class for a single alert channel (sink)."""

    name: str = "alert"

    def __init__(self, settings) -> None:
        self.settings = settings

    def deliver(self, detection) -> None:
        """Deliver one (already score-filtered) detection."""
        raise NotImplementedError

    def close(self, timeout: float = 5.0) -> None:
        """Best-effort flush of any asynchronous delivery this sink queued.

        Default no-op: most sinks (e.g. console) deliver synchronously and
        have nothing to drain. Sinks that queue work on a background thread
        (webhook) must override this so a shutdown right after the last
        detection doesn't silently discard it — see WebhookAlert.close().
        """
        return None


class AlertDispatcher:
    """Filters detections by score and delivers them to all sinks."""

    def __init__(self, settings, sinks) -> None:
        self.settings = settings
        self.sinks = list(sinks)

    def emit(self, detections) -> int:
        """Deliver qualifying detections. Returns how many were emitted."""
        emitted = 0
        for d in detections:
            if d.score < self.settings.alert_min_score:
                continue
            for sink in self.sinks:
                try:
                    sink.deliver(d)
                except Exception:
                    logger.exception("Alert sink %s failed", sink.name)
            emitted += 1
        return emitted

    def close(self, timeout: float = 5.0) -> None:
        """Give every sink a bounded chance to flush queued deliveries.

        Called during shutdown (see Watcher.start()'s finally), before
        main.py's os._exit() — which skips normal interpreter cleanup, so
        anything not drained here is lost, not merely delayed.
        """
        for sink in self.sinks:
            try:
                sink.close(timeout=timeout)
            except Exception:
                logger.exception("Alert sink %s failed to close cleanly", sink.name)
