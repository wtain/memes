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


def test_get_weights_names_missing_keys(monkeypatch):
    from types import SimpleNamespace

    import repository.search_ranking as sr

    ranking = SimpleNamespace(
        SOURCE_WEIGHTS={"ocr": 0.9, "tag": 0.8, "description": 0.6},          # "note" missing
        TIER_WEIGHTS={"exact": 1.0, "stem": 0.8, "fuzzy": 0.5},                # "phonetic" missing
        WARN_MATCH_COUNT=10000,
    )
    monkeypatch.setattr(sr, "settings", SimpleNamespace(SEARCH=SimpleNamespace(RANKING=ranking)))
    sr.get_weights.cache_clear()
    try:
        with pytest.raises(ValueError) as exc:
            sr.get_weights()
    finally:
        sr.get_weights.cache_clear()

    assert "source_weights.note" in str(exc.value)
    assert "tier_weights.phonetic" in str(exc.value)


def _weights_error(monkeypatch, **overrides):
    from types import SimpleNamespace

    import repository.search_ranking as sr

    fields = dict(
        SOURCE_WEIGHTS={"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6},
        TIER_WEIGHTS={"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4},
        WARN_MATCH_COUNT=10000,
    )
    fields.update(overrides)
    ranking = SimpleNamespace(**{k: v for k, v in fields.items() if v is not _ABSENT})
    monkeypatch.setattr(sr, "settings", SimpleNamespace(SEARCH=SimpleNamespace(RANKING=ranking)))
    sr.get_weights.cache_clear()
    try:
        with pytest.raises(ValueError) as exc:
            sr.get_weights()
    finally:
        sr.get_weights.cache_clear()
    return str(exc.value)


_ABSENT = object()
_OK_SOURCES = {"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6}
_OK_TIERS = {"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4}


def test_get_weights_rejects_zero_source_weight(monkeypatch):
    msg = _weights_error(monkeypatch, SOURCE_WEIGHTS={**_OK_SOURCES, "description": 0})
    assert "source_weights.description" in msg


def test_get_weights_rejects_negative_tier_weight(monkeypatch):
    msg = _weights_error(monkeypatch, TIER_WEIGHTS={**_OK_TIERS, "fuzzy": -0.5})
    assert "tier_weights.fuzzy" in msg and "-0.5" in msg


def test_get_weights_rejects_nan_weight(monkeypatch):
    msg = _weights_error(monkeypatch, SOURCE_WEIGHTS={**_OK_SOURCES, "ocr": float("nan")})
    assert "source_weights.ocr" in msg


def test_get_weights_rejects_non_numeric_weight(monkeypatch):
    msg = _weights_error(monkeypatch, TIER_WEIGHTS={**_OK_TIERS, "stem": "high"})
    assert "tier_weights.stem" in msg and "high" in msg


def test_get_weights_rejects_warn_match_count_below_one(monkeypatch):
    msg = _weights_error(monkeypatch, WARN_MATCH_COUNT=0)
    assert "warn_match_count" in msg


def test_get_weights_reports_all_problems_in_one_message(monkeypatch):
    msg = _weights_error(
        monkeypatch,
        SOURCE_WEIGHTS={**_OK_SOURCES, "tag": 0},
        TIER_WEIGHTS={**_OK_TIERS, "exact": -1},
        WARN_MATCH_COUNT=0,
    )
    assert "source_weights.tag" in msg and "tier_weights.exact" in msg and "warn_match_count" in msg


def test_get_weights_missing_ranking_block_is_a_clear_error(monkeypatch):
    from types import SimpleNamespace

    import repository.search_ranking as sr

    monkeypatch.setattr(sr, "settings", SimpleNamespace(SEARCH=SimpleNamespace()))
    sr.get_weights.cache_clear()
    try:
        with pytest.raises(ValueError) as exc:
            sr.get_weights()
    finally:
        sr.get_weights.cache_clear()

    assert "search.ranking missing" in str(exc.value)


def test_get_weights_reads_the_configured_defaults():
    weights = get_weights()

    assert weights.source == {"note": 1.0, "ocr": 0.9, "tag": 0.8, "description": 0.6}
    assert weights.tier == {"exact": 1.0, "stem": 0.8, "fuzzy": 0.5, "phonetic": 0.4}
    assert weights.warn_match_count == 10000
    assert set(weights.source) == set(SOURCES) and set(weights.tier) == set(TIERS)


# --- popularity boost settings (P5, docs/superpowers/specs/2026-10-26-meme-popularity/2026-10-29-p5-ranking-and-trending-design.md)

_NO_BLOCK = object()


def _boost_error(monkeypatch, popularity):
    from types import SimpleNamespace

    import repository.search_ranking as sr

    fields = {} if popularity is _NO_BLOCK else {"POPULARITY": popularity}
    monkeypatch.setattr(sr, "settings", SimpleNamespace(SEARCH=SimpleNamespace(RANKING=SimpleNamespace(**fields))))
    sr.get_popularity_boost.cache_clear()
    try:
        with pytest.raises(ValueError) as exc:
            sr.get_popularity_boost()
    finally:
        sr.get_popularity_boost.cache_clear()
    return str(exc.value)


def _pop(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)


def test_get_popularity_boost_defaults_load():
    from repository.search_ranking import PopularityBoost, get_popularity_boost

    get_popularity_boost.cache_clear()
    boost = get_popularity_boost()
    assert boost == PopularityBoost(weight=0.03, max_log2=5.0)
    assert get_popularity_boost() is boost                       # cached


def test_popularity_boost_missing_block_names_the_keys(monkeypatch):
    msg = _boost_error(monkeypatch, _NO_BLOCK)
    assert "popularity" in msg


def test_popularity_boost_missing_weight(monkeypatch):
    assert "weight" in _boost_error(monkeypatch, _pop(MAX_LOG2=5))


def test_popularity_boost_missing_max_log2(monkeypatch):
    assert "max_log2" in _boost_error(monkeypatch, _pop(WEIGHT=0.03))


@pytest.mark.parametrize("bad", [-0.01, float("nan"), float("inf"), True, "0.03", None])
def test_popularity_boost_rejects_bad_weight(monkeypatch, bad):
    assert "weight" in _boost_error(monkeypatch, _pop(WEIGHT=bad, MAX_LOG2=5))


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "5", None])
def test_popularity_boost_rejects_bad_max_log2(monkeypatch, bad):
    assert "max_log2" in _boost_error(monkeypatch, _pop(WEIGHT=0.03, MAX_LOG2=bad))


def test_popularity_boost_weight_zero_is_valid(monkeypatch):
    from types import SimpleNamespace

    import repository.search_ranking as sr

    ranking = SimpleNamespace(POPULARITY=_pop(WEIGHT=0, MAX_LOG2=5))
    monkeypatch.setattr(sr, "settings", SimpleNamespace(SEARCH=SimpleNamespace(RANKING=ranking)))
    sr.get_popularity_boost.cache_clear()
    try:
        boost = sr.get_popularity_boost()
    finally:
        sr.get_popularity_boost.cache_clear()
    assert boost.weight == 0 and boost.bonus(1000) == 0


def test_popularity_bonus_is_capped_log2():
    from repository.search_ranking import PopularityBoost

    boost = PopularityBoost(weight=0.03, max_log2=5.0)
    assert boost.bonus(1) == 0
    assert boost.bonus(0) == 0
    assert boost.bonus(8) == 0.03 * 3
    assert boost.bonus(2 ** 20) == 0.03 * 5.0
