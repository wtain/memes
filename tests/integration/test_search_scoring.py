"""
Unit tests for repository/search_ranking.py (pure scoring arithmetic and weight loading). No DB rows are
used; the file lives in tests/integration/ only because it imports repository code and that root sets
DATABASE_URL.
"""
import uuid

import pytest

from repository.search_ranking import RankingWeights, SOURCES, TIERS, get_weights, score_images

_W = RankingWeights(
    source={"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6},
    tier={"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4},
    warn_match_count=10000,
)


def test_hit_weight_is_source_times_tier_rounded():
    assert _W.hit("note", "exact") == 1.0
    assert _W.hit("description", "fuzzy") == 0.3
    assert _W.hit("ocr", "stem") == 0.72


def test_score_images_sums_tokens_and_requires_every_token():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    token_hits = [
        {a: 1.0, b: 0.6, c: 0.9},
        {a: 0.5, b: 0.9},          # c has no hit for token 2 -> dropped
    ]

    scores = score_images(token_hits)

    assert scores == {a: 1.5, b: 1.5}


def test_score_images_empty_input_and_empty_token():
    assert score_images([]) == {}
    assert score_images([{uuid.uuid4(): 1.0}, {}]) == {}


def test_score_images_rounds_to_six_decimals():
    a = uuid.uuid4()

    assert score_images([{a: 0.1}, {a: 0.2}, {a: 0.3}])[a] == 0.6   # 0.6000000000000001 unrounded


def test_get_weights_reads_the_configured_defaults():
    weights = get_weights()

    assert weights.source == {"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6}
    assert weights.tier == {"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4}
    assert weights.warn_match_count == 10000
    assert set(weights.source) == set(SOURCES) and set(weights.tier) == set(TIERS)
