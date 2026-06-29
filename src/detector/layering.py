"""
src/detector/layering.py

Layering / spoofing-like detector: a stack of similarly-sized orders on one
side of the book forms a "wall" intended to create the illusion of depth.
We flag a side when one size cluster spans at least ``LAYERING_MIN_LEVELS``
levels on that side.
"""

from __future__ import annotations

from src.detector.base import BaseDetector, Detection, cluster_sizes


class LayeringDetector(BaseDetector):
    name = "layering"

    def analyze(self, snapshot, history) -> list[Detection]:
        detections: list[Detection] = []
        min_levels = self.settings.layering_min_levels
        tolerance = self.settings.repeated_size_tolerance

        for side_name, levels in (("bid", snapshot.bids), ("ask", snapshot.asks)):
            sizes = [lvl.size for lvl in levels if lvl.size > 0]
            if len(sizes) < min_levels:
                continue
            clusters = cluster_sizes(sizes, tolerance)
            top = clusters[0]
            if top.count < min_levels:
                continue

            score = min(1.0, top.count / (min_levels * 2))
            detections.append(
                Detection(
                    detector=self.name,
                    market=snapshot.market,
                    score=score,
                    message=(
                        f"{top.count}-level {side_name} wall at size ~{top.rep:.4g} "
                        f"(threshold {min_levels}) — possible layering/spoofing"
                    ),
                    details={
                        "side": side_name,
                        "wall_size": round(top.rep, 6),
                        "levels": top.count,
                    },
                )
            )
        return detections
