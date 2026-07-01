"""
tests/test_base.py

Unit tests for src.detector.base.cluster_sizes.
"""

from src.detector.base import cluster_sizes


def test_identical_sizes_form_one_cluster():
    clusters = cluster_sizes([10.0, 10.0, 10.0, 10.0], tolerance=0.001)
    assert len(clusters) == 1
    assert clusters[0].count == 4


def test_distinct_sizes_form_separate_clusters():
    clusters = cluster_sizes([1.0, 2.0, 3.0], tolerance=0.001)
    assert len(clusters) == 3


def test_membership_is_anchored_to_first_member_not_a_drifting_mean():
    # Regression: cluster.rep used to be recomputed as the running mean of
    # all members so far. That let a cluster's tolerance window drift away
    # from its first member — e.g. 10.0 and 10.5 join (both within 5% of
    # 10.0), and a naive moving-mean rep of 10.25 would then wrongly let
    # 10.7 join too (0.45 <= 5% of 10.7), even though 10.7 is 7% away from
    # the original anchor. Anchoring to the first member keeps membership
    # independent of how many members were merged before it.
    clusters = cluster_sizes([10.0, 10.5, 10.7], tolerance=0.05)
    counts = sorted((c.count for c in clusters), reverse=True)
    assert counts == [2, 1]

    biggest = clusters[0]
    assert sorted(biggest.sizes) == [10.0, 10.5]
