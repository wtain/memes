from unittest.mock import AsyncMock, MagicMock

import pytest

from Backend.app.services.meme_from_text_service import MemeFromTextService
from Backend.app.types.generated.memefromtextrequest import Schema as Request
from retrieval.facets import RankedItem
from retrieval.model import Candidate, ScoredCandidate
from retrieval.pipeline import InvalidProfile, SearchResult

PROFILE = {"taxonomyVersion": 2, "language": "en", "parserVersion": "p3-x", "degraded": False, "situation": "s",
           "situationConfidence": None, "emotion": [], "stance": [], "topic": [], "tone": []}
META = {"available": True, "parseMs": 1, "embedMs": 2, "retrievalMs": 3, "candidates": 1, "cacheHit": False,
        "parserVersion": "p3-x", "model": "m"}


def _result(items, **overrides):
    values = dict(items=items, facets={"emotion": {"joy": 1}, "stance": {}, "topic": {}, "tone": {}},
                  next_cursor="cur", has_next=True, profile=PROFILE, degraded=False, fallback="none", meta=META)
    values.update(overrides)
    return SearchResult(**values)


def _item(image_id="11111111-1111-1111-1111-111111111111", axis_path=True, cluster_id=4, cluster_size=2):
    candidate = Candidate(image_id, "A situation.", "Use when it fits.", 0.8, {"emotion": (("joy", 0.9),)}, 0.0,
                          axis_path, cluster_id)
    return RankedItem(ScoredCandidate(candidate, 0.75, ()), cluster_size)


def _service(pipeline, basics=None, texts=None, tags=None):
    retrieval_repository = MagicMock()
    retrieval_repository.load_basics = AsyncMock(return_value=basics or {})
    image_repository = MagicMock()
    image_repository.get_texts = AsyncMock(return_value=texts or [])
    image_repository.get_tags = AsyncMock(return_value=tags or [])
    return MemeFromTextService(pipeline, retrieval_repository, image_repository), retrieval_repository


async def test_the_response_hydrates_memes_and_carries_the_pipeline_fields():
    image_id = "11111111-1111-1111-1111-111111111111"
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item(image_id)]))
    texts = [MagicMock(image_id=image_id, text="HELLO", confidence=0.9)]
    tags = [(image_id, "category", "work", "rule")]
    service, repository = _service(pipeline, {image_id: ("a.jpg", False)}, texts, tags)

    response = await service.search(Request(text="hello"))

    assert response.hasNext is True and response.nextCursor == "cur" and response.degraded is False
    result = response.items[0]
    assert result.meme.id == image_id and result.meme.imageUrl == f"/api/images/{image_id}"
    assert result.meme.originalFileName == "a.jpg" and result.meme.text == ["HELLO (0.9)"]
    assert result.meme.tags[0].category == "category" and result.meme.tags[0].name == "work"
    assert result.score == 0.75 and result.axisPath is True and result.clusterId == 4 and result.clusterSize == 2
    assert {f.name for f in response.facets} == {"emotion", "stance", "topic", "tone"}
    assert response.meta.model == "m" and response.profile.language == "en"
    repository.load_basics.assert_awaited_once()


async def test_the_request_is_translated_for_the_pipeline():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([], has_next=False, next_cursor=None))
    service, _ = _service(pipeline)
    request = Request.model_validate({
        "text": "hello", "languageHint": "ru", "cursor": "c", "limit": 7,
        "filters": {"emotion": ["anger"], "tags": [{"key": "category", "value": "work"}]}})

    await service.search(request)

    search_request = pipeline.search.await_args.args[0]
    assert search_request.text == "hello" and search_request.language_hint == "ru"
    assert search_request.cursor == "c" and search_request.limit == 7 and search_request.profile is None
    assert search_request.filters.axes == {"emotion": ("anger",)}
    assert search_request.filters.tags == {"category": ("work",)}


async def test_an_edited_profile_is_passed_as_a_plain_dict():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([], has_next=False, next_cursor=None))
    service, _ = _service(pipeline)

    await service.search(Request.model_validate({"text": "hello", "profile": PROFILE}))

    assert pipeline.search.await_args.args[0].profile["situation"] == "s"


async def test_no_pipeline_returns_the_explicit_unavailable_answer():
    service, repository = _service(None)

    response = await service.search(Request(text="Привет"))

    assert response.items == [] and response.degraded is True and response.fallback == "unavailable"
    assert response.meta.available is False and response.profile.language == "ru"
    repository.load_basics.assert_not_called()


async def test_an_invalid_profile_error_propagates():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(side_effect=InvalidProfile(["emotion: unknown key 'x'"]))
    service, _ = _service(pipeline)
    with pytest.raises(InvalidProfile):
        await service.search(Request.model_validate({"text": "hello", "profile": PROFILE}))


PARSED = {**PROFILE, "language": "ru", "situation": "boss moved the deadline",
          "emotion": [{"key": "stress", "confidence": 0.9, "source": "model"}],
          "topic": [{"key": "work", "confidence": 0.8, "source": "model"}]}


async def test_a_fresh_message_is_recorded_with_parse_and_outcome():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()], profile=PARSED, meta={**META, "queryHash": "a" * 64}))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    service.max_chars = 600
    await service.search(Request.model_validate({"text": "boss moved the deadline"}), user_agent="okhttp/4.12")
    recorder.assert_awaited_once_with(
        kind="meme_text", query="boss moved the deadline", client="android", result_count=1,
        tags=[("emotion", "stress"), ("topic", "work")], language="ru", query_hash="a" * 64,
        situation="boss moved the deadline", degraded=False, fallback=None, parse_ms=1, retrieval_ms=3)


async def test_pages_edits_and_filters_are_not_recorded():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    service.max_chars = 600
    await service.search(Request.model_validate({"text": "x", "cursor": "abc"}))
    await service.search(Request.model_validate({"text": "x", "profile": PROFILE}))
    await service.search(Request.model_validate({"text": "x", "filters": {"emotion": ["joy"]}}))
    recorder.assert_not_awaited()


async def test_a_degraded_search_records_the_fallback_reason():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()], degraded=True, fallback="timeout"))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    service.max_chars = 600
    await service.search(Request.model_validate({"text": "x"}))
    assert recorder.await_args.kwargs["degraded"] is True and recorder.await_args.kwargs["fallback"] == "timeout"


async def test_a_failing_recorder_never_fails_the_search():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    service.history_recorder = AsyncMock(side_effect=RuntimeError("boom"))
    service.max_chars = 600
    response = await service.search(Request.model_validate({"text": "x"}))
    assert response.items


async def test_without_a_recorder_nothing_is_recorded_and_search_works():
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    assert service.history_recorder is None
    assert (await service.search(Request.model_validate({"text": "x"}))).items


async def test_the_unavailable_answer_is_not_recorded():
    service, _ = _service(None)
    recorder = AsyncMock()
    service.history_recorder = recorder
    service.max_chars = 600
    await service.search(Request.model_validate({"text": "x"}))
    recorder.assert_not_awaited()


async def test_the_recorded_query_is_normalised_and_capped():
    from retrieval.query_hash import normalised_query
    pipeline = MagicMock()
    pipeline.search = AsyncMock(return_value=_result([_item()]))
    service, _ = _service(pipeline)
    recorder = AsyncMock()
    service.history_recorder = recorder
    service.max_chars = 50
    text = "  boss   moved\n\tthe deadline  " +"again " * 40
    await service.search(Request.model_validate({"text": text}))
    recorded = recorder.await_args.kwargs["query"]
    assert recorded == normalised_query(text, 50)
    assert len(recorded) <= 50 and "  " not in recorded


def test_a_recorder_without_a_cap_is_rejected():
    with pytest.raises(ValueError):
        MemeFromTextService(None, MagicMock(), MagicMock(), AsyncMock())
