"""Retrieval settings (P4 spec, decision 12). Pure: standard library, parse.config and nothing else."""
import dataclasses
import math
from dataclasses import dataclass
from typing import Mapping

from parse.config import resolve_ollama_url

AXES = ("emotion", "stance", "topic", "tone")
WEIGHT_KEYS = ("situation",) + AXES + ("lexical",)
PREFIX = "search.meme_from_text."

# attribute, key under PREFIX, kind
_SPEC = (
    ("situation_k", "candidates.situation_k", int),
    ("axis_k", "candidates.axis_k", int),
    ("lexical_k", "candidates.lexical_k", int),
    ("total_cap", "candidates.total_cap", int),
    ("neutral_score", "neutral_score", float),
    ("situation_floor", "situation.floor", float),
    ("situation_ceil", "situation.ceil", float),
    ("use_neighbours", "similarity.use_neighbours", bool),
    ("lexical_enabled", "lexical.enabled", bool),
    ("cache_ttl_seconds", "cache_ttl_seconds", float),
    ("embed_timeout_seconds", "embed_timeout_seconds", float),
    ("warm_interval_minutes", "warm_interval_minutes", float),
    ("breaker_failures", "parser_breaker.failures", int),
    ("breaker_open_seconds", "parser_breaker.open_seconds", float),
    ("default_limit", "page.default_limit", int),
    ("max_limit", "page.max_limit", int),
)


@dataclass(frozen=True)
class RetrievalConfig:
    weights: Mapping[str, float]
    situation_k: int
    axis_k: int
    lexical_k: int
    total_cap: int
    neutral_score: float
    situation_floor: float
    situation_ceil: float
    use_neighbours: bool
    lexical_enabled: bool
    cache_ttl_seconds: float
    embed_timeout_seconds: float
    warm_interval_minutes: float
    breaker_failures: int
    breaker_open_seconds: float
    default_limit: int
    max_limit: int
    embedding_model: str
    ollama_url: str

    def with_weights(self, weights: Mapping[str, float]) -> "RetrievalConfig":
        return dataclasses.replace(self, weights=dict(weights))


def _coerce(kind, raw):
    if kind is bool:
        if not isinstance(raw, bool):
            raise ValueError("not a boolean")
        return raw
    if isinstance(raw, bool):
        raise ValueError("a boolean is not a number")
    if kind is int and isinstance(raw, float) and not raw.is_integer():
        raise ValueError("not an integer")
    value = kind(raw)
    if kind is float and not math.isfinite(value):
        raise ValueError("not finite")
    return value


def _problems(values: dict, weights: dict) -> list[str]:
    problems = []
    for name, weight in weights.items():
        if weight < 0:
            problems.append(f"weights.{name} must be >= 0")
    if sum(weights.values()) <= 0:
        problems.append("weights must not all be zero")
    for attribute in ("situation_k", "axis_k", "lexical_k", "total_cap", "breaker_failures", "default_limit",
                      "max_limit"):
        if values[attribute] < 1:
            problems.append(f"{attribute} must be >= 1")
    if not 0.0 <= values["neutral_score"] <= 1.0:
        problems.append("neutral_score must be between 0 and 1")
    if not values["situation_floor"] < values["situation_ceil"]:
        problems.append("situation.floor must be below situation.ceil")
    if values["embed_timeout_seconds"] <= 0:
        problems.append("embed_timeout_seconds must be > 0")
    if values["warm_interval_minutes"] <= 0:
        problems.append("warm_interval_minutes must be > 0")
    if values["cache_ttl_seconds"] < 0 or values["breaker_open_seconds"] < 0:
        problems.append("cache_ttl_seconds and breaker_open_seconds must be >= 0")
    if values["default_limit"] > values["max_limit"]:
        problems.append("page.default_limit must not exceed page.max_limit")
    return problems


def load_config(get, environ=None) -> RetrievalConfig:
    """`get` is `settings.get` (dotted key, optional default). A missing or invalid key is a startup error."""
    missing, invalid, values, weights = [], [], {}, {}
    for attribute, key, kind in _SPEC:
        raw = get(PREFIX + key)
        if raw is None:
            missing.append(PREFIX + key)
            continue
        try:
            values[attribute] = _coerce(kind, raw)
        except (TypeError, ValueError):
            invalid.append(f"{PREFIX + key}={raw!r}")
    for name in WEIGHT_KEYS:
        key = f"{PREFIX}weights.{name}"
        raw = get(key)
        if raw is None:
            missing.append(key)
            continue
        try:
            weights[name] = _coerce(float, raw)
        except (TypeError, ValueError):
            invalid.append(f"{key}={raw!r}")
    model = get("taxonomy.embedding_model")
    url = get("parse.ollama_url")
    if not model:
        missing.append("taxonomy.embedding_model")
    if not url:
        missing.append("parse.ollama_url")
    if missing:
        raise ValueError("search.meme_from_text is missing keys: " + ", ".join(missing))
    if invalid:
        raise ValueError("search.meme_from_text has invalid values: " + ", ".join(invalid))
    problems = _problems(values, weights)
    if problems:
        raise ValueError("search.meme_from_text is invalid: " + "; ".join(problems))
    return RetrievalConfig(weights=weights, embedding_model=str(model),
                           ollama_url=resolve_ollama_url(str(url), environ), **values)
