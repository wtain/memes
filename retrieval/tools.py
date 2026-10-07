"""Typed tool entry points of the retrieval stage (FR-22, P4 spec, decision 8). Pure.

Plain dictionaries in and out so an agent layer can call the same code the endpoint uses; the schemas below are
JSON-schema dictionaries like parse/tool.py's. The endpoint adds `meme` (hydrated from the database) to each item."""
from typing import Mapping

from parse.schema import LANGUAGES
from retrieval.config import AXES, RetrievalConfig
from retrieval.fusion import explanation_dict, fuse, rank_candidates
from retrieval.model import Candidate, Filters, QueryView
from retrieval.pipeline import MemeFromTextPipeline, SearchRequest, SearchResult
from retrieval.similarity import SimilarityIndex

SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "languageHint": {"type": "string", "enum": list(LANGUAGES)},
        "profile": {"$ref": "queryprofile.schema.json"},
        "filters": {"type": "object", "properties": {
            **{axis: {"type": "array", "items": {"type": "string"}} for axis in AXES},
            "tags": {"type": "array", "items": {"type": "object", "properties": {
                "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}}},
        "cursor": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1},
    },
    "required": ["text"],
}

SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": {"type": "object"}},
        "facets": {"type": "array", "items": {"$ref": "facet.schema.json"}},
        "nextCursor": {"type": ["string", "null"]},
        "hasNext": {"type": "boolean"},
        "profile": {"$ref": "queryprofile.schema.json"},
        "degraded": {"type": "boolean"},
        "fallback": {"type": "string"},
        "meta": {"type": "object"},
    },
    "required": ["items", "hasNext", "profile", "degraded", "fallback", "meta"],
}

SCORE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"$ref": "queryprofile.schema.json"},
        "candidates": {"type": "array", "items": {"type": "object"}},
        "lexicalActive": {"type": "boolean"},
    },
    "required": ["candidates", "query"],
}


def _filters(raw) -> Filters:
    if raw is None:
        return Filters()
    if not isinstance(raw, Mapping):
        raise ValueError("filters must be an object")
    axes = {}
    for axis in AXES:
        keys = raw.get(axis)
        if keys is None:
            continue
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise ValueError(f"filters.{axis} must be a list of strings")
        axes[axis] = tuple(keys)
    tags: dict[str, list[str]] = {}
    raw_tags = raw.get("tags")
    if raw_tags is not None and not isinstance(raw_tags, list):
        raise ValueError("filters.tags must be a list of {key, value} objects")
    for pair in raw_tags or []:
        if not isinstance(pair, Mapping) or not isinstance(pair.get("key"), str) or not isinstance(pair.get("value"), str):
            raise ValueError("filters.tags must be a list of {key, value} objects")
        tags.setdefault(pair["key"], []).append(pair["value"])
    return Filters(axes=axes, tags={key: tuple(values) for key, values in tags.items()})


def build_request(arguments) -> SearchRequest:
    if not isinstance(arguments, Mapping):
        raise ValueError("arguments must be an object")
    text = arguments.get("text")
    if not isinstance(text, str):
        raise ValueError("text is required and must be a string")
    hint = arguments.get("languageHint")
    if hint is not None and hint not in LANGUAGES:
        raise ValueError(f"languageHint must be one of {', '.join(LANGUAGES)}")
    limit = arguments.get("limit")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise ValueError("limit must be an integer")
    profile = arguments.get("profile")
    if profile is not None and not isinstance(profile, Mapping):
        raise ValueError("profile must be an object")
    cursor = arguments.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError("cursor must be a string")
    return SearchRequest(text=text, language_hint=hint, profile=profile, filters=_filters(arguments.get("filters")),
                         cursor=cursor, limit=limit)


def result_item_dict(item) -> dict:
    scored = item.scored
    return {
        "imageId": scored.candidate.image_id, "score": scored.score, "axisPath": scored.candidate.axis_path,
        "explanation": explanation_dict(scored), "clusterId": scored.candidate.cluster_id,
        "clusterSize": item.cluster_size,
    }


def facets_list(facets: Mapping[str, Mapping[str, int]]) -> list[dict]:
    return [{"name": axis, "buckets": [{"value": value, "count": count} for value, count in sorted(counts.items())]}
            for axis, counts in facets.items()]


def result_dict(result: SearchResult) -> dict:
    return {
        "items": [result_item_dict(item) for item in result.items], "facets": facets_list(result.facets),
        "nextCursor": result.next_cursor, "hasNext": result.has_next, "profile": result.profile,
        "degraded": result.degraded, "fallback": result.fallback, "meta": result.meta,
    }


async def search_memes_from_text(arguments, pipeline: MemeFromTextPipeline, repository) -> dict:
    """Raises ValueError for a malformed call (and InvalidProfile for an invalid edited profile); model failures
    come back as a degraded result."""
    return result_dict(await pipeline.search(build_request(arguments), repository))


def _candidate_from_dict(raw: Mapping) -> Candidate:
    return Candidate(
        image_id=raw["imageId"], situation_text=raw.get("situationText"), use_when=raw.get("useWhen"),
        cosine=raw.get("cosine"),
        values={axis: tuple((key, float(confidence)) for key, confidence in pairs)
                for axis, pairs in (raw.get("values") or {}).items()},
        lexical=float(raw.get("lexical", 0.0)), axis_path=bool(raw.get("axisPath", True)),
        cluster_id=raw.get("clusterId"))


def score_candidates(arguments: Mapping, config: RetrievalConfig, similarity: SimilarityIndex) -> list[dict]:
    """Score and rank candidate dicts against a query profile (pure; no database, no model)."""
    query = QueryView.from_profile(arguments["query"])
    lexical_active = bool(arguments.get("lexicalActive", False))
    scored = [fuse(_candidate_from_dict(raw), query, config, similarity, lexical_active=lexical_active)
              for raw in arguments["candidates"]]
    return [{"imageId": item.candidate.image_id, "score": item.score, "explanation": explanation_dict(item)}
            for item in rank_candidates(scored)]


def explain_results(arguments: Mapping) -> list[dict]:
    """The explanation objects of already scored results (identity helper kept for the agent layer)."""
    return [item["explanation"] for item in arguments.get("items", [])]
