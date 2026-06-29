"""
src/detector/flicker.py

Order-flicker detector: orders that rapidly appear and disappear at the same
price level ("flickering") are a hallmark of spoofing / quote-stuffing bots.

Within ``FLICKER_WINDOW_SEC`` we count how many times each price level toggles
between present and absent across consecutive snapshots.  A level that toggles
at least ``FLICKER_MIN_EVENTS`` times is flagged.
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection

_PRICE_DECIMALS = 4  # quantization for "same level" matching across snapshots


class FlickerDetector(BaseDetector):
    name = "flicker"

    def analyze(self, snapshot, history) -> list[Detection]:
        window = self.settings.flicker_window_sec
        now = snapshot.timestamp

        snaps = [
            s for s in list(history) + [snapshot] if now - s.timestamp <= window
        ]
        snaps.sort(key=lambda s: s.timestamp)
        if len(snaps) < 3:
            return []

        presence: list[set[float]] = []
        all_keys: set[float] = set()
        for s in snaps:
            keys = {round(lvl.price, _PRICE_DECIMALS) for lvl in s.bids + s.asks}
            presence.append(keys)
            all_keys |= keys

        best_key: float | None = None
        best_transitions = 0
        for key in all_keys:
            seq = [key in keys for keys in presence]
            transitions = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
            if transitions > best_transitions:
                best_transitions = transitions
                best_key = key

        min_events = self.settings.flicker_min_events
        if best_transitions < min_events:
            return []

        score = min(1.0, best_transitions / (min_events * 2))
        return [
            Detection(
                detector=self.name,
                market=snapshot.market,
                score=score,
                message=(
                    f"Level @ {best_key:.4g} toggled {best_transitions}x in "
                    f"{window:.0f}s (threshold {min_events}) — order flicker"
                ),
                details={
                    "price": best_key,
                    "transitions": best_transitions,
                    "window_sec": window,
                },
            )
        ]
