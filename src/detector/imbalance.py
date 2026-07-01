"""
src/detector/imbalance.py

Orderbook-imbalance detector: a strongly one-sided book (most of the resting
size stacked on a single side of the top levels) signals one-sided pressure —
often manipulative quoting rather than genuine two-sided liquidity.

Imbalance over the top ``IMBALANCE_MIN_LEVELS`` levels:

    imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)   in [-1, +1]

Flagged when ``abs(imbalance) >= IMBALANCE_MIN_RATIO``.
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection


class ImbalanceDetector(BaseDetector):
    name = "imbalance"

    def analyze(self, snapshot, history) -> list[Detection]:
        n = self.settings.imbalance_min_levels
        bid_sizes = [lvl.size for lvl in snapshot.bids[:n] if lvl.size > 0]
        ask_sizes = [lvl.size for lvl in snapshot.asks[:n] if lvl.size > 0]
        # Both sides need at least one resting level to compare. A side with
        # zero levels (thin/illiquid market, e.g. right after listing) isn't
        # "manipulative one-sided pressure" -- it's a book with nothing to be
        # imbalanced against -- but the raw ratio below would otherwise peg at
        # score 1.0 forever, firing every tick on a market that's just quiet.
        if not bid_sizes or not ask_sizes:
            return []
        bid_vol = sum(bid_sizes)
        ask_vol = sum(ask_sizes)
        total = bid_vol + ask_vol

        imbalance = (bid_vol - ask_vol) / total
        if abs(imbalance) < self.settings.imbalance_min_ratio:
            return []

        side = "bid" if imbalance > 0 else "ask"
        score = min(1.0, abs(imbalance))
        return [
            Detection(
                detector=self.name,
                market=snapshot.market,
                score=score,
                message=(
                    f"Top-{n} book imbalance {imbalance:+.2f} toward {side} side "
                    f"(threshold {self.settings.imbalance_min_ratio:.2f}) — "
                    f"one-sided pressure"
                ),
                details={
                    "imbalance": round(imbalance, 4),
                    "side": side,
                    "bid_vol": round(bid_vol, 4),
                    "ask_vol": round(ask_vol, 4),
                },
            )
        ]
