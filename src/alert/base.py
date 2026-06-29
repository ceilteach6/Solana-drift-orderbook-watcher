"""
src/alert/base.py

Alert sink interface + a dispatcher that fans a detection out to every
configured sink, after filtering by ``ALERT_MIN_SCORE``.

Add a new channel (Telegram, Discord, PagerDuty, e-mail, …) by subclassing
:class:`Alert` and registering it in :func:`src.alert.build_alert_sinks`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class Alert:
    """Base class for a single alert channel (sink)."""

    name: str = "alert"

    def __init__(self, settings) -> None:
        self.settings = settings

    async def deliver(self, detection) -> None:
        """Deliver one (already score-filtered) detection."""
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by this sink. Override when needed."""


class AlertDispatcher:
    """Filters detections by score and delivers them to all registered sinks."""

    def __init__(self, settings, sinks) -> None:
        self.settings = settings
        self.sinks: list[Alert] = list(sinks)

    async def emit(self, detections) -> int:
        """Deliver qualifying detections to all sinks. Returns how many were emitted."""
        emitted = 0
        for d in detections:
            if d.score < self.settings.alert_min_score:
                continue
            for sink in self.sinks:
                try:
                    await sink.deliver(d)
                except Exception:
                    logger.exception("Alert sink %r failed", sink.name)
            emitted += 1
        return emitted

    def close(self) -> None:
        """Close all sinks and release their resources."""
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:
                logger.debug("Error closing alert sink %r", sink.name, exc_info=True)
