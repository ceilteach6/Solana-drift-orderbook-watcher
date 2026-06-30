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
    rep: float
    sizes: list[float] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.sizes)


def cluster_sizes(sizes, tolerance: float) -> list[_Cluster]:
    """Greedily group near-equal sizes.

    A size joins a cluster when it is within tolerance of that cluster's
    *anchor* (its first, smallest member): ``abs(a - anchor) <= tolerance *
    max(a, anchor)``. The anchor is fixed for the cluster's lifetime rather
    than recomputed as the running mean, so a monotonic run of sizes each
    close to the *previous* one cannot "chain" the cluster arbitrarily far
    from where it started (e.g. 10, 10.5, 11, 11.5, 12 would otherwise all
    merge under a 5% tolerance despite 10 and 12 differing by 20%).
    ``rep`` still tracks the running mean for display purposes only.
    Returns clusters sorted by descending count.
    """
    clusters: list[_Cluster] = []
    for s in sorted(float(x) for x in sizes):
        placed = False
        for c in clusters:
            anchor = c.sizes[0]
            if abs(s - anchor) <= tolerance * max(s, anchor, 1e-9):
                c.sizes.append(s)
                c.rep = sum(c.sizes) / len(c.sizes)
                placed = True
                break
        if not placed:
            clusters.append(_Cluster(rep=s, sizes=[s]))
    clusters.sort(key=lambda c: c.count, reverse=True)
    return clusters
