"""Parser settings (P3 spec, decision 5). Pure: standard library only."""
import os
from dataclasses import dataclass

DEFAULT_OLLAMA_PORT = 11434


@dataclass(frozen=True)
class ParseConfig:
    model: str
    timeout_seconds: float
    retry_min_seconds: float
    max_concurrent: int
    num_ctx: int
    seed: int
    keep_alive: str
    cache_size: int
    max_input_chars: int
    ollama_url: str


def resolve_ollama_url(configured: str, environ=None) -> str:
    """`OLLAMA_HOST` (the Ollama client convention: `host`, `host:port` or a full URL) wins over the setting."""
    environ = os.environ if environ is None else environ
    host = (environ.get("OLLAMA_HOST") or "").strip()
    if not host:
        return configured.rstrip("/")
    if "://" not in host:
        host = "http://" + host
    authority = host.split("://", 1)[1].split("/", 1)[0]
    if ":" not in authority:
        host = host.rstrip("/") + f":{DEFAULT_OLLAMA_PORT}"
    return host.rstrip("/")


def load_config(get, environ=None) -> ParseConfig | None:
    """`get` is `settings.get` (dotted key, optional default). None when `parse.model` is unset, which is how
    `it` (no profiles, out of scope) opts out cleanly."""
    model = get("parse.model")
    if not model:
        return None
    return ParseConfig(
        model=model,
        timeout_seconds=float(get("parse.timeout_seconds")),
        retry_min_seconds=float(get("parse.retry_min_seconds")),
        max_concurrent=int(get("parse.max_concurrent")),
        num_ctx=int(get("parse.num_ctx")),
        seed=int(get("parse.seed")),
        keep_alive=str(get("parse.keep_alive")),
        cache_size=int(get("parse.cache_size")),
        max_input_chars=int(get("parse.max_input_chars")),
        ollama_url=resolve_ollama_url(get("parse.ollama_url"), environ),
    )
