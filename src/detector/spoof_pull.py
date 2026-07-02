"""
src/detector/spoof_pull.py

Spoof-pull detector: the signature of a spoofer is a large "wall" placed on one
side of the book to fake pressure, then **cancelled (pulled)** before it can be
hit — typically as the price moves in the direction the wall was nudging.

This is a refinement above the flicker detector: flicker catches rapid
appear/disappear at a level; spoof-pull specifically catches a *large* wall that
was present a moment ago, is now gone, **and** coincides with a real mid-price
move over the window.

Heuristic per side:
1. Find the biggest wall in the immediately-preceding snapshot — a level whose
   size is at least ``SPOOF_WALL_RATIO`` × the median level size on that side.
2. Check it was *pulled*: the size at that exact price has dropped below
   ``SPOOF_PULL_FRACTION`` of the wall size in the current snapshot.
3. Require a mid-price move of at least ``SPOOF_MIN_PRICE_MOVE`` (relative) over
   the lookback window, so a wall that simply got filled isn't flagged.
"""

from __future__ import annotations

import statistics

from src.detector.base import BaseDetector, Detection

_PRICE_DECIMALS = 4


class SpoofPullDetector(BaseDetector):
    name = "spoof_pull"

    def analyze(self, snapshot, history) -> list[Detection]:
        window = self.settings.spoof_window_sec
        now = snapshot.timestamp

        prior = [s for s in list(history) if now - s.timestamp <= window]
        prior.sort(key=lambda s: s.timestamp)
        if not prior:
            return []

        mid_now = snapshot.mid
        if not mid_now:
            return []

        # For each side, consider every prior snapshot as a possible "wall was
        # here" reference point (not just the immediately-preceding tick).
        # This ties the price-move check to the *same* interval the wall
        # disappeared over, instead of conflating a short pull event with an
        # unrelated price drift measured across the whole window.
        detections: list[Detection] = []
        for side, prev_attr, cur_levels in (
            ("bid", "bids", snapshot.bids),
            ("ask", "asks", snapshot.asks),
        ):
            best: tuple[float, float, float, float, float] | None = None
            for prior_snap in prior:
                mid_then = prior_snap.mid
                if not mid_then:
                    continue
                price_move = (mid_now - mid_then) / mid_then
                if abs(price_move) < self.settings.spoof_min_price_move:
                    continue

                wall = self._find_wall(getattr(prior_snap, prev_attr))
                if wall is None:
                    continue
                price_key, wall_size = wall

                now_size = self._size_at(cur_levels, price_key)
                if now_size >= wall_size * self.settings.spoof_pull_fraction:
                    continue  # wall is still (mostly) there — not pulled

                pulled_fraction = 1.0 - (now_size / wall_size if wall_size else 0.0)
                # Settings validates SPOOF_MIN_PRICE_MOVE > 0, but this
                # detector only assumes a duck-typed settings object (tests
                # and third-party embedders pass a plain SimpleNamespace) —
                # guard the division itself so a misconfigured or
                # hand-built settings object can't turn every wall-pull tick
                # into a ZeroDivisionError.
                min_move = max(self.settings.spoof_min_price_move, 1e-12)
                move_factor = min(1.0, abs(price_move) / (2 * min_move))
                score = min(1.0, 0.4 + 0.6 * max(pulled_fraction, move_factor))

                candidate = (score, price_key, wall_size, now_size, price_move)
                if best is None or score > best[0]:
                    best = candidate

            if best is None:
                continue
            score, price_key, wall_size, now_size, price_move = best
            detections.append(
                Detection(
                    detector=self.name,
                    market=snapshot.market,
                    score=score,
                    message=(
                        f"{side} wall ~{wall_size:.4g} @ {price_key:.4g} pulled while "
                        f"price moved {price_move:+.2%} — possible spoof"
                    ),
                    details={
                        "side": side,
                        "wall_price": price_key,
                        "wall_size": round(wall_size, 6),
                        "remaining_size": round(now_size, 6),
                        "price_move": round(price_move, 6),
                    },
                )
            )
        return detections

    def _find_wall(self, levels):
        nonzero = [lvl for lvl in levels if lvl.size > 0]
        if not nonzero:
            return None
        if len(nonzero) == 1:
            # A single resting level *is* the entire visible side — there's no
            # peer to size it "relative to", but that's not a reason to call
            # it not-a-wall: it disappearing is exactly the "wall pulled"
            # signal, arguably clearer than on a deep book. Skipping this case
            # (the old behavior) silently disabled spoof-pull detection
            # whenever a side thinned out to one level — precisely the
            # sparse-liquidity conditions where a single large wall stands out
            # most and is easiest to spoof.
            lvl = nonzero[0]
            return (round(lvl.price, _PRICE_DECIMALS), lvl.size)
        sizes = [lvl.size for lvl in nonzero]
        median = statistics.median(sizes)
        if median <= 0:
            return None
        threshold = self.settings.spoof_wall_ratio * median
        candidates = [
            (round(lvl.price, _PRICE_DECIMALS), lvl.size)
            for lvl in levels
            if lvl.size >= threshold
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda pc: pc[1])

    @staticmethod
    def _size_at(levels, price_key: float) -> float:
        return sum(
            lvl.size for lvl in levels if round(lvl.price, _PRICE_DECIMALS) == price_key
        )
