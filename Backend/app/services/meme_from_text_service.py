"""Meme-from-text service: runs the pipeline and hydrates the response (P4 spec, decisions 2 and 9)."""
from typing import Optional

from Backend.app.types.generated.memefromtextrequest import Schema as MemeFromTextRequest
from Backend.app.types.generated.memefromtextresponse import Schema as MemeFromTextResponse
from parse.language import guess_language
from parse.parser import normalise_text
from parse.prompt import PARSER_VERSION
from retrieval.model import Filters
from retrieval.query_hash import normalised_query
from retrieval.pipeline import MemeFromTextPipeline, SearchRequest
from retrieval.tools import facets_list, result_item_dict
from taxonomy.loader import load

UNAVAILABLE_META = {"available": False, "parseMs": 0, "embedMs": 0, "retrievalMs": 0, "candidates": 0,
                    "cacheHit": False, "parserVersion": PARSER_VERSION, "model": ""}


def _plain(value):
    """The generated models use str-subclass enums for enum fields; tolerate a plain string too."""
    return getattr(value, "value", value)


def _filters(request: MemeFromTextRequest) -> Filters:
    raw = request.filters
    if raw is None:
        return Filters()
    axes = {axis: tuple(getattr(value, "root", value) for value in getattr(raw, axis) or ()) for axis in ("emotion", "stance", "topic", "tone")
            if getattr(raw, axis)}
    tags: dict[str, list[str]] = {}
    for pair in raw.tags or []:
        tags.setdefault(pair.key, []).append(pair.value)
    return Filters(axes=axes, tags={key: tuple(values) for key, values in tags.items()})


AXES = ("emotion", "stance", "topic", "tone")


def _has_filters(raw) -> bool:
    return raw is not None and any(getattr(raw, name, None) for name in (*AXES, "tags"))


def _is_fresh_message(request: MemeFromTextRequest) -> bool:
    """A new message: not a page of "Show more", not an edited reading, not a filter toggle."""
    return not request.cursor and request.profile is None and not _has_filters(request.filters)


def _history_fields(request: MemeFromTextRequest, result, item_count: int, user_agent: str, max_chars: int) -> dict:
    profile = result.profile or {}
    meta = result.meta or {}
    return {
        "kind": "meme_text",
        "query": normalised_query(request.text, max_chars),
        "client": "android" if "okhttp" in user_agent.lower() else "web",
        "result_count": item_count,
        "tags": [(axis, entry["key"]) for axis in AXES for entry in profile.get(axis) or []],
        "language": profile.get("language"),
        "query_hash": meta.get("queryHash"),
        "situation": profile.get("situation"),
        "degraded": result.degraded,
        "fallback": None if result.fallback in (None, "none") else result.fallback,
        "parse_ms": meta.get("parseMs"),
        "retrieval_ms": meta.get("retrievalMs"),
    }


class MemeFromTextService:
    def __init__(self, pipeline: Optional[MemeFromTextPipeline], retrieval_repository, image_repository,
                 history_recorder=None, max_chars: Optional[int] = None):
        if history_recorder is not None and max_chars is None:
            raise ValueError("max_chars is required when a history recorder is set")
        self.history_recorder = history_recorder
        self.max_chars = max_chars
        self.pipeline = pipeline
        self.retrieval_repository = retrieval_repository
        self.image_repository = image_repository

    async def search(self, request: MemeFromTextRequest, user_agent: str = "") -> MemeFromTextResponse:
        if self.pipeline is None:
            return self._unavailable(request)
        search_request = SearchRequest(
            text=request.text,
            language_hint=_plain(request.languageHint),
            profile=request.profile.model_dump(mode="json", exclude_none=True) if request.profile is not None else None,
            filters=_filters(request), cursor=request.cursor, limit=request.limit)
        result = await self.pipeline.search(search_request, self.retrieval_repository)
        items = [result_item_dict(item) for item in result.items]
        await self._hydrate(items)
        if self.history_recorder is not None and _is_fresh_message(request):
            try:
                await self.history_recorder(**_history_fields(request, result, len(items), user_agent, self.max_chars))
            except Exception:
                pass
        payload = {
            "items": items, "facets": facets_list(result.facets), "hasNext": result.has_next,
            "profile": result.profile, "degraded": result.degraded, "fallback": result.fallback, "meta": result.meta,
        }
        if result.next_cursor:
            payload["nextCursor"] = result.next_cursor
        return MemeFromTextResponse.model_validate(payload)

    async def _hydrate(self, items: list[dict]) -> None:
        """Replace each item's `imageId` by the `meme` object (filename, flagged, OCR text, tags)."""
        ids = {item["imageId"] for item in items}
        basics = await self.retrieval_repository.load_basics(ids) if ids else {}
        texts: dict[str, list[str]] = {}
        tags: dict[str, list[dict]] = {}
        if ids:
            for row in await self.image_repository.get_texts(ids):
                texts.setdefault(str(row.image_id), []).append(f"{row.text} ({row.confidence})")
            for image_id, key, value, source in await self.image_repository.get_tags(ids):
                tags.setdefault(str(image_id), []).append(
                    {"name": value, "category": key, "score": 1, "source": source})
        for item in items:
            image_id = item.pop("imageId")
            filename, flagged = basics.get(image_id, (None, False))
            meme = {"id": image_id, "imageUrl": f"/api/images/{image_id}", "text": texts.get(image_id, []),
                    "tags": tags.get(image_id, []), "flagged": flagged}
            if filename is not None:
                meme["originalFileName"] = filename
            if item.get("clusterId") is not None:
                meme["clusterId"] = item["clusterId"]
            item["meme"] = meme
            for key in ("clusterId", "clusterSize"):
                if item.get(key) is None:
                    item.pop(key, None)

    def _unavailable(self, request: MemeFromTextRequest) -> MemeFromTextResponse:
        tax = load()
        language = _plain(request.languageHint) or guess_language(normalise_text(request.text, 600))
        profile = {"taxonomyVersion": tax.version, "language": language, "parserVersion": PARSER_VERSION,
                   "degraded": True, "emotion": [], "stance": [], "topic": [], "tone": []}
        return MemeFromTextResponse.model_validate({
            "items": [], "facets": [], "hasNext": False, "profile": profile, "degraded": True,
            "fallback": "unavailable", "meta": dict(UNAVAILABLE_META)})
