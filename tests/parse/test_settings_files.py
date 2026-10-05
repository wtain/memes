from pathlib import Path

import yaml

ENVIRONMENTS = Path(__file__).resolve().parents[2] / "environments"


def _load(name):
    return yaml.safe_load((ENVIRONMENTS / name).read_text(encoding="utf-8")) or {}


def test_common_file_has_every_parse_key_with_the_expected_value_types():
    parse = _load("settings.yaml")["parse"]

    assert parse["timeout_seconds"] == 5.0 and parse["retry_min_seconds"] == 1.5
    assert parse["max_concurrent"] == 2 and parse["num_ctx"] == 4096 and parse["seed"] == 42
    assert parse["keep_alive"] == "30m" and parse["cache_size"] == 500 and parse["max_input_chars"] == 600
    assert parse["ollama_url"] == "http://127.0.0.1:11434"
    assert "model" not in parse, "the model lives only in the general and metal overlays"


def test_general_and_metal_configure_a_model_and_it_does_not():
    for name in ("settings.general.yaml", "settings.metal.yaml"):
        assert isinstance(_load(name)["parse"]["model"], str) and _load(name)["parse"]["model"]
    assert "model" not in (_load("settings.it.yaml").get("parse") or {})
