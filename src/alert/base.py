"""
src/alert/base.py

Alert sink interface + a dispatcher that fans a detection out to every
configured sink, after filtering by ``ALERT_MIN_SCORE``.

Add a new channel (e-mail, PagerDuty, ...) by subclassing :class:`Alert` and
registering it in :func:`src.alert.build_alert_sinks`.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class Alert:
    """Base class for a single alert channel (sink)."""

    name: str = "alert"

    def __init__(self, settings) -> None:
        self.settings = settings

    def deliver(self, detection) -> None:
        """Deliver one (already score-filtered) detection."""
        raise NotImplementedError


class AlertDispatcher:
    """Filters detections by score and delivers them to all sinks.

    Also rate-limits repeats of the same (market, detector) within
    ``ALERT_COOLDOWN_SEC`` so a detector that keeps firing every tick can't
    flood the sinks (webhook/Telegram in particular — see
    :class:`src.alert.webhook_alert.WebhookAlert`).
    """

    def __init__(self, settings, sinks) -> None:
        self.settings = settings
        self.sinks = list(sinks)
        self._last_emit: dict[tuple[str, str], float] = {}

    def emit(self, detections) -> int:
        """Deliver qualifying, non-cooled-down detections. Returns how many were emitted."""
        emitted = 0
        now = time.monotonic()
        cooldown = self.settings.alert_cooldown_sec
        for d in detections:
            if d.score < self.settings.alert_min_score:
                continue
            key = (d.market, d.detector)
            last = self._last_emit.get(key)
            if cooldown > 0 and last is not None and (now - last) < cooldown:
                continue
            self._last_emit[key] = now
            for sink in self.sinks:
                try:
                    sink.deliver(d)
                except Exception:
                    logger.exception("Alert sink %s failed", sink.name)
            emitted += 1
        return emitted
