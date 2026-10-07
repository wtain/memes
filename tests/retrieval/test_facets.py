from retrieval.facets import collapse_clusters, facet_counts
from retrieval.fusion import rank_candidates
from retrieval.model import ScoredCandidate
from tests.retrieval.helpers import make_candidate


def _scored(image_id, score, cluster_id=None, values=None):
    return ScoredCandidate(make_candidate(image_id, cluster_id=cluster_id, values=values or {}), score, ())


def test_each_cluster_collapses_to_its_best_member_with_the_candidate_count():
    ranked = rank_candidates([_scored("a", 0.9, 1), _scored("b", 0.8, None), _scored("c", 0.7, 1), _scored("d", 0.6, 2)])

    items = collapse_clusters(ranked)

    assert [(i.scored.candidate.image_id, i.cluster_size) for i in items] == [("a", 2), ("b", None), ("d", 1)]


def test_clusters_with_equal_scores_keep_the_lowest_image_id():
    ranked = rank_candidates([_scored("z", 0.9, 5), _scored("m", 0.9, 5)])
    assert [i.scored.candidate.image_id for i in collapse_clusters(ranked)] == ["m"]


def test_facet_counts_count_each_item_once_per_value_over_the_collapsed_set():
    ranked = rank_candidates([
        _scored("a", 0.9, 1, {"emotion": (("anger", 0.9), ("annoyance", 0.6)), "tone": (("sarcastic", 0.9),)}),
        _scored("b", 0.8, 1, {"emotion": (("anger", 0.9),)}),                 # collapsed away
        _scored("c", 0.7, None, {"emotion": (("anger", 0.3),), "tone": (("dark", 0.6),)}),
    ])

    counts = facet_counts(collapse_clusters(ranked))

    assert counts["emotion"] == {"anger": 2, "annoyance": 1}
    assert counts["tone"] == {"sarcastic": 1, "dark": 1}
    assert counts["stance"] == {} and counts["topic"] == {}
