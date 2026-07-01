"""
src/detector/flicker.py

Order-flicker detector: orders that rapidly appear and disappear at the same
price level ("flickering") are a hallmark of spoofing / quote-stuffing bots.

Within ``FLICKER_WINDOW_SEC``, we count how many times each price level toggles
between present and absent across consecutive snapshots. A level that toggles at
least ``FLICKER_MIN_EVENTS`` times is flagged.
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection

# Price quantization for matching the "same" level across snapshots.
_PRICE_DECIMALS = 4

# A bot quote-stuffing several levels at once should not be collapsed into a
# single alert, but we still cap how many levels get reported per tick so a
# genuinely chaotic book can't flood the alert stream.
_MAX_DETECTIONS_PER_TICK = 5


class FlickerDetector(BaseDetector):
    name = "flicker"

    def analyze(self, snapshot, history) -> list[Detection]:
        window = self.settings.flicker_window_sec
        now = snapshot.timestamp

        snaps = [
            s for s in list(history) + [snapshot] if now - s.timestamp <= window
        ]
        snaps.sort(key=lambda s: s.timestamp)
        if len(snaps) < 3:  # need a few samples to observe toggling
            return []

        # Presence set of (side, quantized_price) tuples per snapshot.
        # Tracking side separately prevents false positives when the same price
        # migrates from bid to ask (e.g. after a large mid-price move).
        presence: list[set[tuple]] = []
        all_keys: set[tuple] = set()
        for s in snaps:
            keys: set[tuple] = set()
            for lvl in s.bids:
                keys.add(("bid", round(lvl.price, _PRICE_DECIMALS)))
            for lvl in s.asks:
                keys.add(("ask", round(lvl.price, _PRICE_DECIMALS)))
            presence.append(keys)
            all_keys |= keys

        min_events = self.settings.flicker_min_events
        flickering: list[tuple[int, tuple]] = []
        for key in all_keys:
            seq = [key in keys for keys in presence]
            transitions = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
            if transitions >= min_events:
                flickering.append((transitions, key))
        if not flickering:
            return []

        # Deterministic ordering: most transitions first, tie-broken by the
        # key itself so results don't depend on set-iteration order.
        flickering.sort(key=lambda t: (-t[0], t[1]))

        detections: list[Detection] = []
        for transitions, (side, price) in flickering[:_MAX_DETECTIONS_PER_TICK]:
            score = min(1.0, transitions / (min_events * 2))
            detections.append(
                Detection(
                    detector=self.name,
                    market=snapshot.market,
                    score=score,
                    message=(
                        f"{side.capitalize()} level @ {price:.4g} toggled {transitions}x in "
                        f"{window:.0f}s (threshold {min_events}) — order flicker"
                    ),
                    details={
                        "side": side,
                        "price": price,
                        "transitions": transitions,
                        "window_sec": window,
                    },
                )
            )
        return detections
