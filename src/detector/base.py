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

    def required_window_sec(self) -> float:
        """Seconds of prior history this detector needs in ``history``.

        The watcher sizes its per-market history buffer from the max of every
        registered detector's declared window, so a detector that looks back
        further than the buffer holds can never silently lose data. Detectors
        that only inspect the current snapshot (no ``history`` look-back)
        should leave this at the default of ``0.0``; detectors that filter
        ``history`` by a time window must override this to return that
        window so the buffer is always sized to cover it.
        """
        return 0.0

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
    """Group near-equal sizes via single-linkage clustering.

    Sizes are sorted, then split into a new cluster only where the gap to the
    immediately preceding (sorted) value exceeds ``tolerance * max(a, b)``
    (relative tolerance). Because membership is decided from the fixed sorted
    order rather than a running average that drifts as items are added, the
    result is order-independent and a smooth ramp of sizes where every
    consecutive pair is within tolerance always collapses into one cluster —
    it can't fragment the way a greedy nearest-running-mean join would.
    Returns clusters sorted by descending count.
    """
    ordered = sorted(float(x) for x in sizes)
    clusters: list[_Cluster] = []
    for s in ordered:
        if clusters and abs(s - clusters[-1].sizes[-1]) <= tolerance * max(
            s, clusters[-1].sizes[-1], 1e-9
        ):
            clusters[-1].sizes.append(s)
        else:
            clusters.append(_Cluster(rep=s, sizes=[s]))
    for c in clusters:
        c.rep = sum(c.sizes) / len(c.sizes)
    clusters.sort(key=lambda c: c.count, reverse=True)
    return clusters
