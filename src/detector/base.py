"""
src/detector/base.py

Detector base class, the :class:`Detection` result type, and a small size
clustering helper shared by the repeated-size and layering detectors.

To add your own detector, subclass :class:`BaseDetector`, implement
``analyze``, and register it in ``src/watcher.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Detection:
    """A single suspicious-pattern finding."""

    detector: str
    market: str
    score: float  # 0.0 (weak) .. 1.0 (strong)
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class BaseDetector:
    """Base class for all detectors."""

    name: str = "base"

    def __init__(self, settings) -> None:
        self.settings = settings

    def analyze(self, snapshot, history) -> list[Detection]:
        """Inspect ``snapshot`` (and prior ``history``) for a pattern.

        Args:
            snapshot: the current :class:`OrderbookSnapshot`.
            history: a sequence of prior snapshots (oldest -> newest), not
                including ``snapshot``.

        Returns:
            A list of :class:`Detection` objects (possibly empty).
        """
        raise NotImplementedError


@dataclass
class _Cluster:
    anchor: float
    sizes: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.sizes)

    @property
    def rep(self) -> float:
        """Representative size for display (mean of members)."""
        return sum(self.sizes) / len(self.sizes)


def cluster_sizes(sizes, tolerance: float) -> list[_Cluster]:
    """Greedily group near-equal sizes.

    Sizes are sorted ascending and each new size is compared against its
    cluster's *anchor* (the first/smallest member), not a running mean: a
    mean drifts upward as members are added, so a slowly-ramping sequence
    (e.g. 1.00, 1.02, 1.04, ... each within tolerance of its neighbor) would
    otherwise fragment into several small clusters instead of one, letting a
    bot with a gradually increasing size schedule dodge detection entirely.
    Anchoring bounds total cluster width to ``tolerance`` of the anchor,
    which is what "near-equal" is supposed to mean.

    Two sizes belong together when ``abs(a - b) <= tolerance * max(a, b)``
    (relative tolerance). Returns clusters sorted by descending count.
    """
    clusters: list[_Cluster] = []
    for s in sorted(float(x) for x in sizes):
        placed = False
        for c in clusters:
            if abs(s - c.anchor) <= tolerance * max(s, c.anchor, 1e-9):
                c.sizes.append(s)
                placed = True
                break
        if not placed:
            clusters.append(_Cluster(anchor=s, sizes=[s]))
    clusters.sort(key=lambda c: c.count, reverse=True)
    return clusters
