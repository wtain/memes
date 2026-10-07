"""Weighted-sum fusion with explanations (P4 spec, decision 4). Pure."""
from typing import Mapping, Sequence

from retrieval.config import RetrievalConfig
from retrieval.model import AxisExplanation, Candidate, QueryView, ScoredCandidate
from retrieval.scoring import axis_score, situation_score
from retrieval.similarity import SimilarityIndex


def fuse(candidate: Candidate, query: QueryView, config: RetrievalConfig, similarity: SimilarityIndex, *,
         lexical_active: bool) -> ScoredCandidate:
    """score = sum over present axes of (w_a / sum of w over present axes) x s_a. Absent query axes are not
    scored (FR-15); a meme with no profile is neutral on every present axis but keeps its real lexical score.
    When the present axes carry no weight at all (a zero-weight fallback axis), they are weighted equally
    instead of everything scoring 0.0, so the ranking still follows the real signal."""
    present = query.present_axes(lexical_active=lexical_active)
    if not present:
        return ScoredCandidate(candidate, 0.0, ())
    total_weight = sum(config.weights[axis] for axis in present)
    equal = total_weight <= 0
    explanations, score = [], 0.0
    for axis in present:
        weight = 1.0 / len(present) if equal else config.weights[axis] / total_weight
        matched = ()
        if axis == "situation":
            if candidate.axis_path:
                value, neutral = situation_score(candidate.cosine, config.situation_floor, config.situation_ceil,
                                                 config.neutral_score)
            else:
                value, neutral = config.neutral_score, True
            query_values = (query.situation,)
        elif axis == "lexical":
            value, neutral, query_values = candidate.lexical, False, ()
        else:
            query_pairs = query.axes[axis]
            query_values = tuple(key for key, _confidence in query_pairs)
            if candidate.axis_path:
                value, neutral, matched = axis_score(axis, query_pairs, candidate.values.get(axis, ()), similarity,
                                                     config.neutral_score, config.use_neighbours)
            else:
                value, neutral = config.neutral_score, True
        contribution = weight * value
        score += contribution
        explanations.append(AxisExplanation(axis, round(value, 6), round(weight, 6), round(contribution, 6),
                                            neutral, query_values, matched))
    return ScoredCandidate(candidate, round(score, 6), tuple(explanations))


def rank_candidates(scored: Sequence[ScoredCandidate]) -> list[ScoredCandidate]:
    return sorted(scored, key=lambda item: (-item.score, item.candidate.image_id))


def explanation_dict(scored: ScoredCandidate) -> dict:
    """The public `explanation` object of a result (shared schema memefromtextresult)."""
    return {
        "axes": [
            {
                "axis": axis.axis, "score": axis.score, "weight": axis.weight, "contribution": axis.contribution,
                "neutral": axis.neutral, "queryValues": list(axis.query_values),
                "matched": [{"queryKey": m.query_key, "memeKey": m.meme_key, "similarity": m.similarity,
                             "memeConfidence": m.meme_confidence} for m in axis.matched],
            }
            for axis in scored.axes
        ],
        "situationText": scored.candidate.situation_text,
        "useWhen": scored.candidate.use_when,
    }
