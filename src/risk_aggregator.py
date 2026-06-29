"""
src/risk_aggregator.py

Per-market risk aggregator: smooths detection scores with an exponential
moving average (EMA) so the alert layer sees a stable "suspicion level"
rather than noisy per-tick spikes.

Usage inside the watcher loop::

    agg = RiskAggregator(settings)
    smoothed = agg.update(market, detections)
    if agg.is_elevated(market):
        # composite alert — risk is persistently high
        ...
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RiskAggregator:
    """EMA-smoothed, per-market risk score tracker.

    alpha = RISK_EMA_ALPHA controls how fast the score reacts:
      - high alpha (close to 1) → fast, noisy
      - low alpha (close to 0) → slow, stable
    Default 0.30 gives a half-life of roughly 2 ticks.
    """

    def __init__(self, settings) -> None:
        self.settings = settings
        self._ema: dict[str, float] = {}

    def update(self, market: str, detections) -> float:
        """Fold one tick's detections into the market EMA.

        Takes the max score across *all* detections in this tick (so a single
        strong signal lifts the EMA) then applies exponential smoothing.

        Returns the new smoothed score (0.0 – 1.0).
        """
        tick_score = max((d.score for d in detections), default=0.0)
        alpha = self.settings.risk_ema_alpha
        prev = self._ema.get(market, 0.0)
        smoothed = alpha * tick_score + (1.0 - alpha) * prev
        self._ema[market] = smoothed

        return smoothed

    def score(self, market: str) -> float:
        """Current smoothed risk score for *market* (0.0 – 1.0)."""
        return self._ema.get(market, 0.0)

    def is_elevated(self, market: str) -> bool:
        """True when the smoothed score is at or above the composite threshold."""
        return self.score(market) >= self.settings.risk_composite_threshold

    def all_scores(self) -> dict[str, float]:
        """Snapshot of every tracked market's current EMA score."""
        return dict(self._ema)
