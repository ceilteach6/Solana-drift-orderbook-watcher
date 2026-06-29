"""
src/detector/session_baseline.py

Session-baseline / volume-spike detector.

Tracks a per-market exponential moving average (EMA) of total resting volume
(bids + asks).  Once the EMA is warm, it flags two anomalies:

* **Volume spike** — current volume > EMA × BASELINE_SPIKE_RATIO
  Sudden depth explosion often precedes wash-trading or fake liquidity injection.

* **Volume drain** — current volume < EMA × BASELINE_DRAIN_RATIO
  Sudden depth collapse signals mass cancellation — a classic spoofing exit.

The EMA approach means the baseline adapts to slowly changing market
conditions (time of day, volatility regime) without manual tuning.

Settings
--------
BASELINE_WARMUP_CYCLES  (int,   default 20)  — snapshots before flagging
BASELINE_SPIKE_RATIO    (float, default 3.0) — spike multiplier threshold
BASELINE_DRAIN_RATIO    (float, default 0.2) — drain fraction threshold
BASELINE_EMA_ALPHA      (float, default 0.1) — EMA smoothing factor (0, 1]
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection


class SessionBaselineDetector(BaseDetector):
    name = "volume_spike"

    def __init__(self, settings) -> None:
        super().__init__(settings)
        # EMA state keyed by market name.
        self._ema: dict[str, float] = {}
        self._cycles: dict[str, int] = {}

    def analyze(self, snapshot, history) -> list[Detection]:
        market = snapshot.market
        alpha = self.settings.baseline_ema_alpha
        spike_ratio = self.settings.baseline_spike_ratio
        drain_ratio = self.settings.baseline_drain_ratio
        warmup = self.settings.baseline_warmup_cycles

        current_vol = sum(lvl.size for lvl in snapshot.bids + snapshot.asks if lvl.size > 0)
        n = self._cycles.get(market, 0)

        if market not in self._ema:
            # First snapshot — seed the EMA, nothing to compare against yet.
            self._ema[market] = current_vol
            self._cycles[market] = 1
            return []

        # Use the EMA from *before* this snapshot so the anomaly isn't diluted.
        prev_ema = self._ema[market]
        self._ema[market] = alpha * current_vol + (1 - alpha) * prev_ema
        self._cycles[market] = n + 1

        if n < warmup or prev_ema <= 0:
            return []

        ratio = current_vol / prev_ema

        if ratio >= spike_ratio:
            score = min(1.0, 0.6 + (ratio - spike_ratio) / (spike_ratio * 2))
            return [
                Detection(
                    detector=self.name,
                    market=market,
                    score=round(score, 4),
                    message=(
                        f"Volume spike {ratio:.1f}× above EMA baseline "
                        f"(threshold {spike_ratio:.1f}×) — possible fake depth injection"
                    ),
                    details={
                        "ratio": round(ratio, 3),
                        "current_vol": round(current_vol, 2),
                        "ema_baseline": round(prev_ema, 2),
                        "kind": "spike",
                    },
                )
            ]

        if ratio <= drain_ratio:
            score = min(1.0, 0.6 + (drain_ratio - ratio) / drain_ratio)
            return [
                Detection(
                    detector=self.name,
                    market=market,
                    score=round(score, 4),
                    message=(
                        f"Volume drain to {ratio:.2f}× of EMA baseline "
                        f"(threshold {drain_ratio:.2f}×) — possible mass cancellation"
                    ),
                    details={
                        "ratio": round(ratio, 3),
                        "current_vol": round(current_vol, 2),
                        "ema_baseline": round(prev_ema, 2),
                        "kind": "drain",
                    },
                )
            ]

        return []
