import pytest

from parse.config import ParseConfig, load_config, resolve_ollama_url

VALUES = {
    "parse.model": "qwen2.5:7b-instruct", "parse.timeout_seconds": 5.0, "parse.retry_min_seconds": 1.5,
    "parse.max_concurrent": 2, "parse.num_ctx": 4096, "parse.seed": 42, "parse.keep_alive": "30m",
    "parse.cache_size": 500, "parse.max_input_chars": 600, "parse.ollama_url": "http://127.0.0.1:11434",
}


def _get(values):
    return lambda key, default=None: values.get(key, default)


def test_load_config_reads_every_key():
    config = load_config(_get(VALUES), environ={})

    assert config == ParseConfig(
        model="qwen2.5:7b-instruct", timeout_seconds=5.0, retry_min_seconds=1.5, max_concurrent=2, num_ctx=4096,
        seed=42, keep_alive="30m", cache_size=500, max_input_chars=600, ollama_url="http://127.0.0.1:11434")


def test_load_config_is_none_when_no_model_is_configured():
    values = {key: value for key, value in VALUES.items() if key != "parse.model"}
    assert load_config(_get(values), environ={}) is None
    assert load_config(_get({**VALUES, "parse.model": ""}), environ={}) is None


def test_ollama_host_env_wins_over_the_configured_url():
    assert resolve_ollama_url("http://127.0.0.1:11434", {}) == "http://127.0.0.1:11434"
    assert resolve_ollama_url("http://127.0.0.1:11434/", {}) == "http://127.0.0.1:11434"
    assert resolve_ollama_url("http://a:1", {"OLLAMA_HOST": "http://gpu-box:9999"}) == "http://gpu-box:9999"
    assert resolve_ollama_url("http://a:1", {"OLLAMA_HOST": "gpu-box:9999"}) == "http://gpu-box:9999"
    assert resolve_ollama_url("http://a:1", {"OLLAMA_HOST": "gpu-box"}) == "http://gpu-box:11434"
    assert resolve_ollama_url("http://a:1", {"OLLAMA_HOST": "  "}) == "http://a:1"


def test_numeric_values_are_coerced():
    config = load_config(_get({**VALUES, "parse.timeout_seconds": "7", "parse.max_concurrent": "3"}), environ={})
    assert config.timeout_seconds == 7.0 and config.max_concurrent == 3
