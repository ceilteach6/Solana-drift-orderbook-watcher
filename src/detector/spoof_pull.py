"""
src/detector/spoof_pull.py

Spoof-pull detector: a large order wall appears on one side of the book,
then vanishes — and the price moves in the *opposite* direction immediately
after (i.e. the wall was fake, creating the illusion of depth).

Pattern (bid-side example):
  tick N  : heavy bid volume in top SPOOF_PULL_LEVELS levels
  tick N+1: bid volume collapses by >= SPOOF_PULL_VOL_RATIO (50 % default)
             AND mid price drops by >= SPOOF_PULL_PRICE_DELTA (0.1 % default)

This is a history-based detector and requires at least two prior snapshots.
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection


class SpoofPullDetector(BaseDetector):
    name = "spoof_pull"

    def analyze(self, snapshot, history) -> list[Detection]:
        if len(history) < 1:
            return []

        prev = history[-1]
        curr = snapshot

        if prev.mid is None or curr.mid is None:
            return []

        n = self.settings.spoof_pull_levels
        vol_ratio = self.settings.spoof_pull_vol_ratio
        price_delta = self.settings.spoof_pull_price_delta

        price_change = (curr.mid - prev.mid) / prev.mid  # signed fraction

        detections: list[Detection] = []

        # Bid wall pulled → price drops  (price_sign = -1)
        # Ask wall pulled → price rises  (price_sign = +1)
        for side_name, prev_levels, curr_levels, price_sign in (
            ("bid", prev.bids, curr.bids, -1),
            ("ask", prev.asks, curr.asks, +1),
        ):
            prev_vol = sum(lvl.size for lvl in prev_levels[:n] if lvl.size > 0)
            curr_vol = sum(lvl.size for lvl in curr_levels[:n] if lvl.size > 0)

            if prev_vol <= 0:
                continue

            collapse = 1.0 - curr_vol / prev_vol
            if collapse < vol_ratio:
                continue  # wall didn't collapse enough

            # price moved away from where the fake wall was
            # bid wall → expect price_change < 0 → price_change * (-1) > 0
            # ask wall → expect price_change > 0 → price_change * (+1) > 0
            if price_change * price_sign < price_delta:
                continue  # price didn't move in expected direction

            score = min(1.0, collapse * (price_change * price_sign) / price_delta)
            detections.append(
                Detection(
                    detector=self.name,
                    market=snapshot.market,
                    score=score,
                    message=(
                        f"Spoof-pull: {side_name} wall collapsed "
                        f"({prev_vol:.2f} → {curr_vol:.2f}, -{collapse:.0%}) "
                        f"while price moved {price_change:+.3%} — probable fake wall"
                    ),
                    details={
                        "side": side_name,
                        "prev_vol": round(prev_vol, 4),
                        "curr_vol": round(curr_vol, 4),
                        "collapse_ratio": round(collapse, 4),
                        "price_change_pct": round(price_change * 100, 4),
                    },
                )
            )

        return detections
