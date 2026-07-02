import numpy as np
from hdbscan import HDBSCAN


def build_clusters_from_embeddings(
    keys: list[str],
    embeddings: np.ndarray,
    min_cluster_size: int,
    min_samples: int | None = None,
    cluster_selection_epsilon: float = 0.0,
    cluster_selection_method: str = "eom",
) -> dict[int, list[str]]:
    """
    Run HDBSCAN over L2-normalised embeddings (metric='euclidean' — for
    normalised vectors this is a monotonic function of cosine similarity,
    since ||a-b||^2 = 2 - 2*cos_sim(a,b)). Returns {label: [keys]}, where
    label -1 is HDBSCAN's noise bucket (maps directly to "singletons").

    min_samples is forwarded to HDBSCAN unmodified: when None, HDBSCAN
    applies its own library default, which equals min_cluster_size.

    cluster_selection_method: HDBSCAN's own parameter, 'eom' (default,
    excess-of-mass — prefers fewer, larger, more stable clusters) or 'leaf'
    (selects finer leaf clusters from the condensed tree instead of merging
    them into their most stable ancestor — produces more, smaller, tighter
    clusters at the cost of more points landing in noise).

    Caller must ensure len(keys) >= 2 and embeddings are already L2-normalised.
    """
    clusterer = HDBSCAN(
        metric="euclidean",
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        cluster_selection_epsilon=cluster_selection_epsilon,
        cluster_selection_method=cluster_selection_method,
    )
    labels = clusterer.fit_predict(embeddings)

    groups: dict[int, list[str]] = {}
    for key, label in zip(keys, labels):
        groups.setdefault(int(label), []).append(key)
    return groups
