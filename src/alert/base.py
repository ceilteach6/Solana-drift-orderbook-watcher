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
                # Logged (not just silently dropped): when this is a risk
                # aggregator Detection, the aggregator has already latched
                # its own internal "alerting"/cooldown state believing the
                # alert went out. A misconfigured ALERT_MIN_SCORE above
                # RISK_ALERT_THRESHOLD would otherwise desync those two
                # gates with zero diagnostic signal — see Settings
                # .__post_init__ for the belt-and-suspenders config check.
                logger.debug(
                    "Dropping %s detection for %s: score %.3f < ALERT_MIN_SCORE %.3f",
                    d.detector, d.market, d.score, self.settings.alert_min_score,
                )
                continue
            for sink in self.sinks:
                try:
                    sink.deliver(d)
                except Exception:
                    logger.exception("Alert sink %s failed", sink.name)
            emitted += 1
        return emitted
