import pytest

from retrieval.config import AXES, WEIGHT_KEYS, RetrievalConfig, load_config

VALUES = {
    "search.meme_from_text.weights.situation": 0.4, "search.meme_from_text.weights.emotion": 0.15,
    "search.meme_from_text.weights.stance": 0.1, "search.meme_from_text.weights.topic": 0.15,
    "search.meme_from_text.weights.tone": 0.1, "search.meme_from_text.weights.lexical": 0.1,
    "search.meme_from_text.candidates.situation_k": 300, "search.meme_from_text.candidates.axis_k": 300,
    "search.meme_from_text.candidates.lexical_k": 100, "search.meme_from_text.candidates.total_cap": 600,
    "search.meme_from_text.neutral_score": 0.3, "search.meme_from_text.situation.floor": 0.45,
    "search.meme_from_text.situation.ceil": 0.85, "search.meme_from_text.similarity.use_neighbours": True,
    "search.meme_from_text.lexical.enabled": True, "search.meme_from_text.lexical.max_tokens": 20,
    "search.meme_from_text.lexical.max_tokens": 20,
    "search.meme_from_text.lexical.only_when_degraded": True,
    "search.meme_from_text.cache_ttl_seconds": 120,
    "search.meme_from_text.embed_timeout_seconds": 2.0, "search.meme_from_text.warm_interval_minutes": 20,
    "search.meme_from_text.parser_breaker.failures": 3, "search.meme_from_text.parser_breaker.open_seconds": 30,
    "search.meme_from_text.page.default_limit": 20, "search.meme_from_text.page.max_limit": 50,
    "taxonomy.embedding_model": "bge-large", "parse.ollama_url": "http://127.0.0.1:11434",
}


def _get(values):
    return lambda key, default=None: values.get(key, default)


def test_load_config_reads_every_key():
    config = load_config(_get(VALUES), environ={})

    assert isinstance(config, RetrievalConfig)
    assert config.weights == {"situation": 0.4, "emotion": 0.15, "stance": 0.1, "topic": 0.15, "tone": 0.1,
                              "lexical": 0.1}
    assert (config.situation_k, config.axis_k, config.lexical_k, config.total_cap) == (300, 300, 100, 600)
    assert (config.neutral_score, config.situation_floor, config.situation_ceil) == (0.3, 0.45, 0.85)
    assert config.use_neighbours is True and config.lexical_enabled is True
    assert config.lexical_only_when_degraded is True
    assert (config.cache_ttl_seconds, config.embed_timeout_seconds, config.warm_interval_minutes) == (120.0, 2.0, 20.0)
    assert (config.breaker_failures, config.breaker_open_seconds) == (3, 30.0)
    assert (config.default_limit, config.max_limit) == (20, 50)
    assert config.embedding_model == "bge-large" and config.ollama_url == "http://127.0.0.1:11434"


def test_constants():
    assert AXES == ("emotion", "stance", "topic", "tone")
    assert WEIGHT_KEYS == ("situation", "emotion", "stance", "topic", "tone", "lexical")


def test_ollama_host_environment_wins():
    config = load_config(_get(VALUES), environ={"OLLAMA_HOST": "gpu-box:9999"})
    assert config.ollama_url == "http://gpu-box:9999"


def test_a_missing_key_is_a_startup_error_naming_the_key():
    values = {k: v for k, v in VALUES.items() if k != "search.meme_from_text.weights.stance"}
    with pytest.raises(ValueError, match="weights.stance"):
        load_config(_get(values), environ={})


@pytest.mark.parametrize("key, bad, fragment", [
    ("search.meme_from_text.weights.tone", -0.1, "weights.tone"),
    ("search.meme_from_text.weights.tone", "x", "weights.tone"),
    ("search.meme_from_text.neutral_score", 1.5, "neutral_score"),
    ("search.meme_from_text.situation.ceil", 0.40, "floor"),
    ("search.meme_from_text.candidates.total_cap", 0, "total_cap"),
    ("search.meme_from_text.candidates.axis_k", 2.5, "axis_k"),
    ("search.meme_from_text.similarity.use_neighbours", "yes", "use_neighbours"),
    ("search.meme_from_text.page.default_limit", 100, "default_limit"),
    ("search.meme_from_text.embed_timeout_seconds", 0, "embed_timeout_seconds"),
    ("search.meme_from_text.parser_breaker.failures", 0, "failures"),
])
def test_invalid_values_are_rejected(key, bad, fragment):
    with pytest.raises(ValueError, match=fragment):
        load_config(_get({**VALUES, key: bad}), environ={})


def test_all_weights_zero_is_rejected():
    values = {**VALUES, **{f"search.meme_from_text.weights.{name}": 0 for name in WEIGHT_KEYS}}
    with pytest.raises(ValueError, match="weights"):
        load_config(_get(values), environ={})


def test_with_weights_replaces_only_the_weights():
    config = load_config(_get(VALUES), environ={})
    changed = config.with_weights({**config.weights, "situation": 0.9})
    assert changed.weights["situation"] == 0.9 and changed.neutral_score == config.neutral_score
    assert config.weights["situation"] == 0.4


def test_zero_situation_weight_is_rejected():
    values = {**VALUES, "search.meme_from_text.weights.situation": 0}
    with pytest.raises(ValueError, match="weights.situation must be > 0"):
        load_config(_get(values), environ={})


def test_zero_lexical_weight_is_rejected_only_while_lexical_is_enabled():
    values = {**VALUES, "search.meme_from_text.weights.lexical": 0}
    with pytest.raises(ValueError, match="weights.lexical must be > 0"):
        load_config(_get(values), environ={})
    disabled = load_config(_get({**values, "search.meme_from_text.lexical.enabled": False}), environ={})
    assert disabled.weights["lexical"] == 0


def test_lexical_only_when_degraded_is_required_and_boolean():
    key = "search.meme_from_text.lexical.only_when_degraded"
    assert load_config(_get({**VALUES, key: False}), environ={}).lexical_only_when_degraded is False
    with pytest.raises(ValueError, match="only_when_degraded"):
        load_config(_get({k: v for k, v in VALUES.items() if k != key}), environ={})
    with pytest.raises(ValueError, match="only_when_degraded"):
        load_config(_get({**VALUES, key: "yes"}), environ={})


def test_lexical_max_tokens_is_read_and_must_be_a_positive_integer():
    values = {**VALUES, "search.meme_from_text.lexical.max_tokens": 20}
    assert load_config(_get(values), environ={}).lexical_max_tokens == 20
    with pytest.raises(ValueError, match="lexical.max_tokens"):
        load_config(_get({**values, "search.meme_from_text.lexical.max_tokens": 0}), environ={})
    missing = {k: v for k, v in values.items() if not k.endswith("lexical.max_tokens")}
    with pytest.raises(ValueError, match="lexical.max_tokens"):
        load_config(_get(missing), environ={})
