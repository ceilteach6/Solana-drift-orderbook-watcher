"""
tests/test_base.py

Unit tests for src/detector/base.py's cluster_sizes helper.
"""

from src.detector.base import cluster_sizes


def test_exact_duplicates_cluster_together():
    clusters = cluster_sizes([10.0, 10.0, 10.0, 5.0], 0.01)
    assert clusters[0].count == 3
    assert clusters[0].sizes == [10.0, 10.0, 10.0]


def test_does_not_chain_past_tolerance_of_anchor():
    # Each adjacent pair is within 5% of the previous, but a running-mean
    # clusterer would let the cluster "walk" until 10 and 12 (a 20% gap)
    # end up in the same bucket. The anchor-based clusterer must not do that.
    sizes = [10.0, 10.5, 11.0, 11.5, 12.0]
    clusters = cluster_sizes(sizes, 0.05)
    assert len(clusters) > 1
    for c in clusters:
        anchor = min(c.sizes)
        for s in c.sizes:
            assert abs(s - anchor) <= 0.05 * max(s, anchor)


def test_singletons_each_own_cluster():
    clusters = cluster_sizes([1.0, 50.0, 1000.0], 0.001)
    assert len(clusters) == 3
    assert all(c.count == 1 for c in clusters)
