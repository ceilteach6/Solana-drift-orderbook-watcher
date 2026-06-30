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

        best_key: tuple | None = None
        best_transitions = 0
        for key in all_keys:
            seq = [key in keys for keys in presence]
            transitions = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
            if transitions > best_transitions:
                best_transitions = transitions
                best_key = key

        if best_key is None or best_transitions <= 0:
            return []  # nothing ever toggled — no level to report

        min_events = self.settings.flicker_min_events
        if best_transitions < min_events:
            return []

        side, price = best_key
        # Clamp the denominator: a non-positive threshold means "flag any
        # toggling", not "divide by zero".
        score = min(1.0, best_transitions / (max(min_events, 1) * 2))
        return [
            Detection(
                detector=self.name,
                market=snapshot.market,
                score=score,
                message=(
                    f"{side.capitalize()} level @ {price:.4g} toggled {best_transitions}x in "
                    f"{window:.0f}s (threshold {min_events}) — order flicker"
                ),
                details={
                    "side": side,
                    "price": price,
                    "transitions": best_transitions,
                    "window_sec": window,
                },
            )
        ]
