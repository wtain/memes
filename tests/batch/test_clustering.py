import numpy as np

from batch.utils.clustering import build_clusters_from_embeddings


# Two tight trios of points (each trio close to one of two orthogonal
# directions) plus one clear outlier in the opposite direction of the first
# trio. min_samples is left as None throughout so HDBSCAN applies its own
# default (min_samples == min_cluster_size) — trios of 3 are required
# because, under that real default with min_cluster_size=2, pairs of only 2
# close points are not dense enough to form a cluster and collapse to noise.
_TRIO_KEYS = ["a1", "a2", "a3", "b1", "b2", "b3", "outlier"]
_TRIO_EMBEDDINGS = np.array([
    [1.0, 0.0],       # a1: points in +x
    [0.99, 0.05],     # a2: close to a1
    [0.98, -0.05],    # a3: close to a1
    [0.0, 1.0],       # b1: points in +y (orthogonal to a-trio)
    [0.05, 0.99],     # b2: close to b1
    [-0.05, 0.98],    # b3: close to b1
    [-1.0, 0.0],      # outlier: opposite direction to a-trio
])


def test_build_clusters_from_embeddings_groups_close_points_separately():
    # L2-normalize embeddings as per function contract
    embeddings = _TRIO_EMBEDDINGS / np.linalg.norm(_TRIO_EMBEDDINGS, axis=1, keepdims=True)

    # min_samples left unset (None) -> HDBSCAN applies its own default
    # (min_samples == min_cluster_size == 2), which is dense enough for the
    # two trios of close points to form clusters.
    groups = build_clusters_from_embeddings(_TRIO_KEYS, embeddings, min_cluster_size=2)

    non_noise = {frozenset(members) for label, members in groups.items() if label != -1}
    assert frozenset({"a1", "a2", "a3"}) in non_noise
    assert frozenset({"b1", "b2", "b3"}) in non_noise
    assert groups[-1] == ["outlier"]


def test_build_clusters_from_embeddings_respects_min_cluster_size():
    # Same points as above, but require a min_cluster_size (4) larger than
    # either trio (3 points each) -> no group can meet the threshold, so
    # everything -- including the two otherwise-clusterable trios -- lands
    # in the noise bucket.
    embeddings = _TRIO_EMBEDDINGS / np.linalg.norm(_TRIO_EMBEDDINGS, axis=1, keepdims=True)

    groups = build_clusters_from_embeddings(_TRIO_KEYS, embeddings, min_cluster_size=4)

    assert set(groups.keys()) == {-1}
    assert sorted(groups[-1]) == sorted(_TRIO_KEYS)
