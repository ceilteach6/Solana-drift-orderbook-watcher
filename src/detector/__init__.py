"""Bot-pattern detector layer."""

from src.detector.base import BaseDetector, Detection, cluster_sizes
from src.detector.flicker import FlickerDetector
from src.detector.imbalance import ImbalanceDetector
from src.detector.layering import LayeringDetector
from src.detector.repeated_size import RepeatedSizeDetector
from src.detector.spoof_pull import SpoofPullDetector

#: Default detector stack, instantiated by the watcher.
DEFAULT_DETECTORS = (
    RepeatedSizeDetector,
    LayeringDetector,
    FlickerDetector,
    ImbalanceDetector,
    SpoofPullDetector,
)

__all__ = [
    "BaseDetector",
    "Detection",
    "cluster_sizes",
    "RepeatedSizeDetector",
    "LayeringDetector",
    "FlickerDetector",
    "ImbalanceDetector",
    "SpoofPullDetector",
    "DEFAULT_DETECTORS",
]
