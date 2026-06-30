"""
src/detector/repeated_size.py

Repeated-size detector: a classic bot signature is many resting orders of the
*exact same* size (within a tight tolerance) scattered across the book.
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection, cluster_sizes


class RepeatedSizeDetector(BaseDetector):
    name = "repeated_size"

    def analyze(self, snapshot, history) -> list[Detection]:
        sizes = [lvl.size for lvl in snapshot.bids + snapshot.asks if lvl.size > 0]
        if not sizes:
            return []

        clusters = cluster_sizes(sizes, self.settings.repeated_size_tolerance)
        top = clusters[0]
        min_count = max(self.settings.repeated_min_count, 1)
        if top.count < min_count:
            return []

        score = min(1.0, top.count / (min_count * 2))
        return [
            Detection(
                detector=self.name,
                market=snapshot.market,
                score=score,
                message=(
                    f"{top.count} orders share size ~{top.rep:.4g} "
                    f"(threshold {min_count}) — bot-like repetition"
                ),
                details={"repeated_size": round(top.rep, 6), "count": top.count},
            )
        ]
