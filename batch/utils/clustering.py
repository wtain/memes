import numpy as np
from hdbscan import HDBSCAN


def build_clusters_from_embeddings(
    keys: list[str],
    embeddings: np.ndarray,
    min_cluster_size: int,
    min_samples: int | None = None,
    cluster_selection_epsilon: float = 0.0,
) -> dict[int, list[str]]:
    """
    Run HDBSCAN over L2-normalised embeddings (metric='euclidean' — for
    normalised vectors this is a monotonic function of cosine similarity,
    since ||a-b||^2 = 2 - 2*cos_sim(a,b)). Returns {label: [keys]}, where
    label -1 is HDBSCAN's noise bucket (maps directly to "singletons").

    Caller must ensure len(keys) >= 2 and embeddings are already L2-normalised.
    """
    if min_samples is None:
        min_samples = 1
    clusterer = HDBSCAN(
        metric="euclidean",
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_epsilon=cluster_selection_epsilon,
    )
    labels = clusterer.fit_predict(embeddings)

    groups: dict[int, list[str]] = {}
    for key, label in zip(keys, labels):
        groups.setdefault(int(label), []).append(key)
    return groups
