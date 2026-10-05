"""The query parser (P3 spec, decision 2). Pure apart from the Ollama call made through the client.

The parser never raises: every failure becomes a degraded result with a `meta.fallback` reason. The user's text
is never logged; only the structured line below and exception type names are.
"""
import asyncio
import hashlib
import logging
import time
import unicodedata
from dataclasses import dataclass, replace

from parse.answer import UnusableAnswer, interpret_answer
from parse.cache import ParseCache
from parse.client import OllamaChatClient, ParserBadResponse, ParserTimeout, ParserUnavailable
from parse.config import ParseConfig
from parse.language import guess_language
from parse.prompt import PARSER_VERSION, RETRY_TEMPLATE, build_messages
from parse.schema import build_response_schema
from taxonomy.loader import load
from taxonomy.model import Taxonomy

logger = logging.getLogger("parse")


@dataclass(frozen=True)
class ParseMeta:
    model: str
    parser_version: str
    latency_ms: int
    cache_hit: bool
    attempts: int
    fallback: str          # none | retry | unavailable | timeout | invalid_output | empty_input


@dataclass(frozen=True)
class ParseResult:
    profile: dict
    meta: ParseMeta


def normalise_text(text: str, max_chars: int) -> str:
    text = unicodedata.normalize("NFC", text)
    text = " ".join(text.split())
    return text[:max_chars].rstrip()


def degraded_result(text: str, hint, tax: Taxonomy, fallback: str, *, model: str, parser_version: str,
                    attempts: int = 0, latency_ms: int = 0) -> ParseResult:
    profile = {
        "taxonomyVersion": tax.version, "language": guess_language(text, hint), "parserVersion": parser_version,
        "degraded": True, "situation": None, "situationConfidence": None,
    }
    for axis in tax.controlled_axes():
        profile[axis] = []
    return ParseResult(profile, ParseMeta(model, parser_version, latency_ms, False, attempts, fallback))


def meta_to_dict(meta: ParseMeta) -> dict:
    return {"model": meta.model, "parserVersion": meta.parser_version, "latencyMs": meta.latency_ms,
            "cacheHit": meta.cache_hit, "attempts": meta.attempts, "fallback": meta.fallback}


def log_result(result: ParseResult) -> None:
    meta, profile = result.meta, result.profile
    logger.info(
        "parse model=%s parser_version=%s taxonomy_version=%s language=%s degraded=%s fallback=%s "
        "attempts=%s cache_hit=%s latency_ms=%s",
        meta.model, meta.parser_version, profile["taxonomyVersion"], profile["language"], profile["degraded"],
        meta.fallback, meta.attempts, meta.cache_hit, meta.latency_ms)


class QueryParser:
    def __init__(self, config: ParseConfig, client=None, tax: Taxonomy | None = None, *,
                 parser_version: str = PARSER_VERSION, clock=time.monotonic):
        self._config = config
        self._tax = tax or load()
        self._version = parser_version
        self._schema = build_response_schema(self._tax)
        self._client = client or OllamaChatClient(
            config.ollama_url, seed=config.seed, num_ctx=config.num_ctx, keep_alive=config.keep_alive)
        self._cache = ParseCache(config.cache_size)
        self._gate_state: tuple | None = None      # (loop, semaphore): a semaphore binds to one event loop
        self._clock = clock

    def _gate_for_running_loop(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._gate_state is None or self._gate_state[0] is not loop:
            self._gate_state = (loop, asyncio.Semaphore(self._config.max_concurrent))
        return self._gate_state[1]

    async def warm_up(self, timeout_seconds: float = 120.0) -> bool:
        """One fixed, harmless call that loads the model. Caches nothing, never raises, logs no text."""
        text = "warm up the model"
        try:
            await self._client.achat(self._config.model, build_messages(text, self._tax), self._schema,
                                     timeout_seconds)
        except Exception as error:
            logger.warning("warm-up failed: %s", type(error).__name__)
            return False
        return True

    @property
    def model(self) -> str:
        return self._config.model

    def cache_key(self, normalised: str) -> tuple:
        digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
        return (digest, self._version, self._tax.version, self._config.model)

    async def parse(self, text: str, language_hint: str | None = None) -> ParseResult:
        start = self._clock()
        try:
            result = await self._parse(text, language_hint, start)
        except Exception as error:      # the parser never raises (FR-4, NFR-4); asyncio cancellation is not caught
            logger.warning("parse failed unexpectedly: %s", type(error).__name__)
            result = self._degraded(text, language_hint, "unavailable", 0, start)
        self._log(result)
        return result

    async def _parse(self, text: str, hint, start: float) -> ParseResult:
        normalised = normalise_text(text, self._config.max_input_chars)
        if not normalised:
            return self._degraded(text, hint, "empty_input", 0, start)
        key = self.cache_key(normalised)
        cached = self._cache.get(key)
        if cached is not None:
            return ParseResult(cached.profile, replace(cached.meta, cache_hit=True, latency_ms=self._elapsed(start)))

        deadline = start + self._config.timeout_seconds
        try:
            gate = self._gate_for_running_loop()
            await asyncio.wait_for(gate.acquire(), timeout=max(deadline - self._clock(), 0))
        except asyncio.TimeoutError:
            return self._degraded(normalised, hint, "timeout", 0, start)
        try:
            profile, fallback, attempts = await self._attempts(normalised, deadline)
        finally:
            gate.release()
        if profile is None:
            return self._degraded(normalised, hint, fallback, attempts, start)
        result = ParseResult(profile, ParseMeta(
            self._config.model, self._version, self._elapsed(start), False, attempts, fallback))
        self._cache.put(key, result)
        return result

    async def _attempts(self, normalised: str, deadline: float):
        messages = build_messages(normalised, self._tax)
        attempts = 0
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:
                return None, "timeout", attempts
            attempts += 1
            try:
                raw = await self._client.achat(self._config.model, messages, self._schema, remaining)
            except ParserTimeout:
                return None, "timeout", attempts
            except (ParserUnavailable, ParserBadResponse):
                return None, "unavailable", attempts
            try:
                profile = interpret_answer(raw, self._tax, self._version)
                return profile, ("none" if attempts == 1 else "retry"), attempts
            except UnusableAnswer as error:
                if attempts >= 2 or deadline - self._clock() < self._config.retry_min_seconds:
                    return None, "invalid_output", attempts
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": RETRY_TEMPLATE.format(error=str(error))},
                ]

    def _degraded(self, text: str, hint, fallback: str, attempts: int, start: float) -> ParseResult:
        return degraded_result(text, hint, self._tax, fallback, model=self._config.model,
                               parser_version=self._version, attempts=attempts, latency_ms=self._elapsed(start))

    def _elapsed(self, start: float) -> int:
        return int((self._clock() - start) * 1000)

    def _log(self, result: ParseResult) -> None:
        log_result(result)
