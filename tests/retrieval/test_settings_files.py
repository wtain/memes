from pathlib import Path

import pytest
import yaml

from retrieval.config import WEIGHT_KEYS, load_config

SETTINGS = Path(__file__).resolve().parents[2] / "environments" / "settings.yaml"


def _dotted(data, prefix=""):
    flat = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and not value.get("__leaf__"):
            flat.update(_dotted(value, name + "."))
        flat[name] = value
    return flat


@pytest.fixture(scope="module")
def flat():
    return _dotted(yaml.safe_load(SETTINGS.read_text(encoding="utf-8")))


def test_the_shipped_settings_load_and_match_the_documented_defaults(flat):
    config = load_config(lambda key, default=None: flat.get(key, default), environ={})

    assert config.weights == {"situation": 0.40, "emotion": 0.15, "stance": 0.10, "topic": 0.15, "tone": 0.10,
                              "lexical": 0.10}
    assert set(config.weights) == set(WEIGHT_KEYS)
    assert (config.situation_k, config.axis_k, config.lexical_k, config.total_cap) == (300, 300, 100, 600)
    assert (config.neutral_score, config.situation_floor, config.situation_ceil) == (0.3, 0.45, 0.85)
    assert config.use_neighbours is True and config.lexical_enabled is True and config.lexical_max_tokens == 20
    assert (config.cache_ttl_seconds, config.embed_timeout_seconds, config.warm_interval_minutes) == (120, 2.0, 20)
    assert (config.breaker_failures, config.breaker_open_seconds, config.default_limit, config.max_limit) == (3, 30, 20, 50)
    assert config.embedding_model == "bge-large"
