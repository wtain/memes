"""The meme-from-text retrieval pipeline (P4 spec, decisions 2, 6 and 7). Pure: stdlib, parse, taxonomy.

Services are injected: a parser with `async parse(text, hint)`, an embedder with
`async aembed(model, texts, timeout)`, and, per call, a repository with `async generate_candidates(**kwargs)`.
The user's text is never logged; only the structured line below and exception type names are."""
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Mapping

from parse.language import guess_language
from parse.parser import degraded_result, normalise_text
from parse.prompt import PARSER_VERSION
from retrieval.config import RetrievalConfig
from retrieval.cursor import fingerprint, paginate
from retrieval.facets import RankedItem, collapse_clusters, facet_counts
from retrieval.fusion import fuse, rank_candidates
from retrieval.model import Candidate, Filters, QueryView
from retrieval.similarity import SimilarityIndex
from taxonomy.loader import load
from taxonomy.model import Taxonomy
from taxonomy.validate import validate_profile

logger = logging.getLogger("retrieval")

CACHE_ENTRIES = 50
VECTOR_CACHE_ENTRIES = 200
WARM_TIMEOUT_SECONDS = 120.0
DEGRADED_CACHE_TTL_SECONDS = 10.0   # a degraded answer must not outlive a transient failure for long
BREAKER_FALLBACKS = ("unavailable", "timeout")


class InvalidProfile(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


@dataclass(frozen=True)
class SearchRequest:
    text: str
    language_hint: str | None = None
    profile: Mapping | None = None
    filters: Filters = field(default_factory=Filters)
    cursor: str | None = None
    limit: int | None = None
    restrict_ids: frozenset | None = None       # evaluation only: restrict every source to these image ids


@dataclass(frozen=True)
class Prepared:
    profile: dict
    view: QueryView
    candidates: list[Candidate]
    lexical_active: bool
    degraded: bool
    fallback: str
    parse_ms: int
    embed_ms: int
    retrieval_ms: int
    parser_version: str
    model: str


@dataclass(frozen=True)
class _Entry:
    items: list[RankedItem]
    facets: dict[str, dict[str, int]]
    prepared: Prepared


@dataclass(frozen=True)
class SearchResult:
    items: list[RankedItem]
    facets: dict[str, dict[str, int]]
    next_cursor: str | None
    has_next: bool
    profile: dict
    degraded: bool
    fallback: str
    meta: dict


class MemeFromTextPipeline:
    def __init__(self, config: RetrievalConfig, parser, embedder, similarity: SimilarityIndex,
                 tax: Taxonomy | None = None, *, max_input_chars: int = 600, clock=time.monotonic):
        self._config = config
        self._parser = parser
        self._embedder = embedder
        self._similarity = similarity
        self._tax = tax or load()
        self._max_chars = max_input_chars
        self._clock = clock
        self._cache: OrderedDict[str, tuple[float, _Entry]] = OrderedDict()
        self._vectors: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._failures = 0
        self._open_until = 0.0

    # ------------------------------------------------------------------ public

    @property
    def config(self) -> RetrievalConfig:
        return self._config

    async def search(self, request: SearchRequest, repository) -> SearchResult:
        started = self._clock()
        limit = request.limit if request.limit else self._config.default_limit
        limit = max(1, min(limit, self._config.max_limit))
        text = normalise_text(request.text, self._max_chars)
        fp = self._fingerprint(request, text)
        entry = self._cache_get(fp)
        cache_hit = entry is not None
        if entry is None:
            prepared = await self.prepare(request, repository, text=text)
            entry = self.rank(prepared)
            self._cache_put(fp, entry)
        page, next_cursor, has_next = paginate(entry.items, request.cursor, fp, limit)
        prepared = entry.prepared
        meta = {
            "available": True,
            "parseMs": 0 if cache_hit else prepared.parse_ms,
            "embedMs": 0 if cache_hit else prepared.embed_ms,
            "retrievalMs": int((self._clock() - started) * 1000) if cache_hit else prepared.retrieval_ms,
            "candidates": len(prepared.candidates),
            "cacheHit": cache_hit,
            "parserVersion": prepared.parser_version,
            "model": prepared.model,
        }
        result = SearchResult(page, entry.facets, next_cursor, has_next, prepared.profile, prepared.degraded,
                              prepared.fallback, meta)
        logger.info("retrieval degraded=%s fallback=%s candidates=%s results=%s cache_hit=%s parse_ms=%s "
                    "embed_ms=%s retrieval_ms=%s", result.degraded, result.fallback, meta["candidates"],
                    len(page), cache_hit, meta["parseMs"], meta["embedMs"], meta["retrievalMs"])
        return result

    async def prepare(self, request: SearchRequest, repository, *, text: str | None = None) -> Prepared:
        """Resolve the profile, embed, generate candidates (everything before scoring)."""
        if text is None:
            text = normalise_text(request.text, self._max_chars)
        t0 = self._clock()
        profile, edited, parse_fallback = await self._resolve_profile(request, text)
        t1 = self._clock()
        view = QueryView.from_profile(profile)
        parse_degraded = bool(profile["degraded"]) and not edited
        embed_text = (text or None) if parse_degraded else view.situation
        vector, embed_failed = await self._embed(embed_text)
        t2 = self._clock()
        scoring_view = view
        if vector is None:
            # no query vector (nothing to embed, or the embedding failed): the situation axis cannot be scored
            scoring_view = QueryView(situation=None, axes=view.axes, language=view.language, degraded=view.degraded)
        elif parse_degraded:
            scoring_view = QueryView(situation=text, axes={}, language=view.language, degraded=True)
        lexical_text = text if (self._config.lexical_enabled and text) else None
        candidates = await repository.generate_candidates(
            query_vector=vector, query_axes=scoring_view.axes,
            neighbours=self._similarity.neighbours_for(scoring_view.axes) if self._config.use_neighbours else None,
            lexical_text=lexical_text, filters=request.filters, restrict_ids=request.restrict_ids,
            model_name=self._config.embedding_model, situation_k=self._config.situation_k,
            axis_k=self._config.axis_k, lexical_k=self._config.lexical_k, total_cap=self._config.total_cap)
        t3 = self._clock()
        lexical_active = lexical_text is not None and any(candidate.lexical > 0 for candidate in candidates)
        fallback = parse_fallback if parse_fallback not in ("none", "retry") else "none"
        if embed_failed:
            fallback = "embed_unavailable" if fallback == "none" else f"{fallback}+embed_unavailable"
        return Prepared(
            profile=profile, view=scoring_view, candidates=candidates, lexical_active=lexical_active,
            degraded=fallback != "none", fallback=fallback, parse_ms=int((t1 - t0) * 1000),
            embed_ms=int((t2 - t1) * 1000), retrieval_ms=int((t3 - t2) * 1000), parser_version=self._parser_version(),
            model=getattr(self._parser, "model", ""))

    def rank(self, prepared: Prepared, config: RetrievalConfig | None = None) -> _Entry:
        config = config or self._config
        if not prepared.view.present_axes(lexical_active=prepared.lexical_active):
            return _Entry([], {axis: {} for axis in ("emotion", "stance", "topic", "tone")}, prepared)
        scored = [fuse(candidate, prepared.view, config, self._similarity, lexical_active=prepared.lexical_active)
                  for candidate in prepared.candidates]
        items = collapse_clusters(rank_candidates(scored))
        return _Entry(items, facet_counts(items), prepared)

    async def warm_up(self) -> bool:
        """One embedding call so the model is loaded; never raises."""
        try:
            await self._embedder.aembed(self._config.embedding_model, ["warm up the embedding model"],
                                        WARM_TIMEOUT_SECONDS)
            return True
        except Exception as error:
            logger.info("embedding warm-up failed: %s", type(error).__name__)
            return False

    # ------------------------------------------------------------------ internals

    def _parser_version(self) -> str:
        return PARSER_VERSION

    async def _resolve_profile(self, request: SearchRequest, text: str):
        """(profile dict, edited?, parse fallback)."""
        if request.profile is not None:
            result = validate_profile(request.profile, self._tax)
            if result.errors:
                raise InvalidProfile(result.errors)
            language = request.profile.get("language") or request.language_hint or guess_language(text)
            profile = {"taxonomyVersion": self._tax.version, "language": language, "parserVersion": "edited",
                       "degraded": False, **result.profile}
            return profile, True, "none"
        if self._clock() < self._open_until:
            parsed = degraded_result(text, request.language_hint, self._tax, "breaker_open",
                                     model=getattr(self._parser, "model", ""), parser_version=PARSER_VERSION)
        else:
            parsed = await self._parser.parse(text, request.language_hint)
            self._record(parsed.meta.fallback)
        return dict(parsed.profile), False, parsed.meta.fallback

    def _record(self, fallback: str) -> None:
        if fallback in BREAKER_FALLBACKS:
            self._failures += 1
            if self._failures >= self._config.breaker_failures:
                self._open_until = self._clock() + self._config.breaker_open_seconds
        else:
            self._failures = 0

    async def _embed(self, text: str | None):
        """(vector or None, failed?). No text means no embedding and no failure."""
        if not text:
            return None, False
        key = (self._config.embedding_model, text)
        if key in self._vectors:
            self._vectors.move_to_end(key)
            return self._vectors[key], False
        try:
            vectors = await self._embedder.aembed(self._config.embedding_model, [text],
                                                  self._config.embed_timeout_seconds)
            vector = vectors[0]
        except Exception as error:
            logger.warning("embedding failed: %s", type(error).__name__)
            return None, True
        self._vectors[key] = vector
        while len(self._vectors) > VECTOR_CACHE_ENTRIES:
            self._vectors.popitem(last=False)
        return vector, False

    def _fingerprint(self, request: SearchRequest, text: str) -> str:
        restriction = None
        if request.restrict_ids is not None:
            restriction = hashlib.sha256(",".join(sorted(str(i) for i in request.restrict_ids)).encode()).hexdigest()
        config_hash = hashlib.sha256(repr(self._config).encode()).hexdigest()
        return fingerprint({
            "t": text, "h": request.language_hint, "p": request.profile, "f": request.filters.to_dict(), "r": restriction,
            "pv": PARSER_VERSION, "tv": self._tax.version, "c": config_hash,
        })

    def _cache_get(self, fp: str) -> _Entry | None:
        item = self._cache.get(fp)
        if item is None:
            return None
        expires, entry = item
        if self._clock() >= expires:
            del self._cache[fp]
            return None
        self._cache.move_to_end(fp)
        return entry

    def _cache_put(self, fp: str, entry: _Entry) -> None:
        ttl = self._config.cache_ttl_seconds
        if ttl <= 0:
            return
        if entry.prepared.degraded:
            ttl = min(ttl, DEGRADED_CACHE_TTL_SECONDS)
        self._cache[fp] = (self._clock() + ttl, entry)
        self._cache.move_to_end(fp)
        while len(self._cache) > CACHE_ENTRIES:
            self._cache.popitem(last=False)
