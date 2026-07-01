"""
src/risk/aggregator.py

Risk aggregator: collapses the per-tick detector findings for a market into a
single, time-smoothed risk level — and emits a *consolidated* alert instead of
one alert per detection.

Why: the raw detectors fire on every snapshot, which is noisy. The aggregator
turns that stream into a stable "how suspicious is this market right now?"
signal.

How:
1. **Combine** this tick's detections into an instantaneous risk in [0, 1] using
   a noisy-OR over the strongest score per detector (multiple simultaneous
   signals reinforce each other).
2. **Smooth** with an EMA (``RISK_SMOOTHING``) so a single odd tick can't
   trigger an alert — the level has to be *sustained*.
3. **Gate** emission with hysteresis + cooldown:
   - raise an alert when the smoothed level crosses ``RISK_ALERT_THRESHOLD``
   - stay quiet (no re-spam) until ``RISK_ALERT_COOLDOWN_SEC`` elapses
   - clear the elevated state only when it drops below ``RISK_CLEAR_THRESHOLD``
     (lower than the alert threshold → no flapping at the boundary)
"""

from __future__ import annotations

import math

from src.detector.base import Detection


class RiskAggregator:
    name = "risk"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._score: dict[str, float] = {}      # smoothed risk per market
        self._alerting: dict[str, bool] = {}    # in elevated state?
        self._last_emit: dict[str, float] = {}  # ts of last emitted alert

    def update(self, market: str, timestamp: float, detections) -> Detection | None:
        """Feed one tick's detections; maybe return a consolidated risk alert."""
        by_detector = self._strongest_per_detector(detections)
        instant = self._noisy_or(by_detector.values())

        alpha = self.settings.risk_smoothing
        prev = self._score.get(market, 0.0)
        smoothed = alpha * instant + (1 - alpha) * prev
        self._score[market] = smoothed

        if self._alerting.get(market, False):
            if smoothed < self.settings.risk_clear_threshold:
                self._alerting[market] = False  # cleared; go quiet
                return None
            # Still elevated — re-emit only after the cooldown.
            last = self._last_emit.get(market, float("-inf"))
            if timestamp - last >= self.settings.risk_alert_cooldown_sec:
                return self._build(market, smoothed, by_detector, timestamp)
            return None

        if smoothed >= self.settings.risk_alert_threshold:
            self._alerting[market] = True
            return self._build(market, smoothed, by_detector, timestamp)
        return None

    def score(self, market: str) -> float:
        """Current smoothed risk for a market (0.0 if unseen)."""
        return self._score.get(market, 0.0)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _strongest_per_detector(detections) -> dict[str, float]:
        out: dict[str, float] = {}
        for d in detections:
            if not math.isfinite(d.score):
                # A NaN/inf score (a buggy or future detector) must not enter
                # the EMA below — min()/max() pass NaN through unchanged, and
                # once the smoothed score goes NaN it stays NaN forever,
                # silently disabling risk alerting for that market.
                continue
            score = min(max(d.score, 0.0), 1.0)
            if score > out.get(d.detector, 0.0):
                out[d.detector] = score
        return out

    @staticmethod
    def _noisy_or(scores) -> float:
        product = 1.0
        for s in scores:
            product *= 1 - s
        return 1 - product

    def _build(self, market, smoothed, by_detector, timestamp) -> Detection:
        self._last_emit[market] = timestamp
        breakdown = {k: round(v, 3) for k, v in sorted(by_detector.items())}
        contributors = ", ".join(f"{k}={v:.2f}" for k, v in breakdown.items())
        contributors = contributors or "decaying (no active signals)"
        return Detection(
            detector=self.name,
            market=market,
            score=min(smoothed, 1.0),
            message=(
                f"Risk level {smoothed:.2f} on {market} "
                f"(alert ≥ {self.settings.risk_alert_threshold:.2f}) — {contributors}"
            ),
            details={
                "risk": round(smoothed, 4),
                "contributors": breakdown,
            },
        )
