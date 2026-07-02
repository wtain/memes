import numpy as np

from batch.utils.clustering import build_clusters_from_embeddings


def test_build_clusters_from_embeddings_groups_close_points_separately():
    keys = ["a", "b", "c", "d", "outlier"]
    # Create normalized vectors: two close pairs and one orthogonal outlier
    embeddings = np.array([
        [1.0, 0.0],      # a: points in +x
        [0.99, 0.1],     # b: close to a
        [0.0, 1.0],      # c: points in +y (orthogonal to a)
        [0.1, 0.99],     # d: close to c
        [-1.0, 0.0],     # outlier: opposite direction to a
    ])
    # L2-normalize embeddings as per function contract
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    groups = build_clusters_from_embeddings(keys, embeddings, min_cluster_size=2)

    non_noise = {frozenset(members) for label, members in groups.items() if label != -1}
    assert frozenset({"a", "b"}) in non_noise
    assert frozenset({"c", "d"}) in non_noise
    assert groups[-1] == ["outlier"]


def test_build_clusters_from_embeddings_respects_min_cluster_size():
    keys = ["a", "b", "c", "d"]
    embeddings = np.array([
        [1.0, 0.0],
        [0.99, 0.01],
        [0.0, 1.0],
        [10.0, 10.0],
    ])
    # L2-normalize embeddings as per function contract
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

    groups = build_clusters_from_embeddings(keys, embeddings, min_cluster_size=3)

    # No group of size >= 3 exists among 4 well-separated points -> everything is noise.
    assert set(groups.keys()) == {-1}
    assert sorted(groups[-1]) == ["a", "b", "c", "d"]
