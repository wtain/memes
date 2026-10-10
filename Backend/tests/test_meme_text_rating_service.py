import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from Backend.app.services.meme_text_rating_service import (
    MemeTextRatingService, RatingValidationError, UnknownImageError,
)
from Backend.app.types.generated.ratingrequest import Schema as RatingRequest
from retrieval.query_hash import normalised_query, query_hash

IMAGE = str(uuid.uuid4())


def _request(**overrides):
    values = dict(text="  My manager   scheduled a meeting ", imageId=IMAGE, grade=2, rank=3, reading="parsed",
                  parserVersion="p3-x", taxonomyVersion=2, degraded=False, fallback="none", language="en")
    values.update(overrides)
    return RatingRequest.model_validate(values)


def _service(active=True):
    repository = MagicMock()
    repository.image_is_active = AsyncMock(return_value=active)
    repository.upsert = AsyncMock(side_effect=lambda **kwargs: kwargs["grade"])
    repository.delete = AsyncMock(return_value=True)
    repository.grades_for = AsyncMock(return_value=[(uuid.UUID(IMAGE), 2)])
    repository.forget_text = AsyncMock(return_value=2)
    return MemeTextRatingService(repository, 600), repository


async def test_put_stores_the_normalised_text_and_the_server_computed_hash():
    service, repository = _service()

    response = await service.put(_request())

    stored = repository.upsert.await_args.kwargs
    expected_text = normalised_query("  My manager   scheduled a meeting ", 600)
    assert stored["query_text"] == expected_text and stored["query_hash"] == query_hash(expected_text)
    assert (stored["grade"], stored["rank"], stored["reading"], stored["language"]) == (2, 3, "parsed", "en")
    assert stored["image_id"] == uuid.UUID(IMAGE) and stored["taxonomy_version"] == 2
    assert (response.queryHash, response.imageId, response.grade) == (stored["query_hash"], IMAGE, 2)


async def test_put_truncates_like_the_search_endpoint():
    service, repository = _service()
    await service.put(_request(text="x" * 700))
    assert repository.upsert.await_args.kwargs["query_text"] == "x" * 600


async def test_blank_text_is_rejected_without_touching_the_repository():
    service, repository = _service()
    with pytest.raises(RatingValidationError):
        await service.put(_request(text="   \n  "))
    repository.upsert.assert_not_called()


async def test_a_nul_character_is_rejected_with_a_fixed_message():
    service, repository = _service()
    with pytest.raises(RatingValidationError) as raised:
        await service.put(_request(text="hello\x00SECRET"))
    assert str(raised.value) == "the message contains an invalid character"
    repository.upsert.assert_not_called()


async def test_an_unknown_or_inactive_image_is_rejected():
    service, repository = _service(active=False)
    with pytest.raises(UnknownImageError):
        await service.put(_request())
    repository.upsert.assert_not_called()


async def test_a_malformed_image_id_is_a_validation_error():
    service, _ = _service()
    with pytest.raises(RatingValidationError):
        await service.put(_request(imageId="not-a-uuid"))


async def test_delete_list_and_forget_validate_the_hash():
    service, repository = _service()
    good = "a" * 64

    assert (await service.list(good)).ratings[0].grade == 2
    await service.delete(good, IMAGE)
    await service.forget_text(good)

    repository.delete.assert_awaited_once_with(good, uuid.UUID(IMAGE))
    repository.forget_text.assert_awaited_once_with(good)
    for bad in ("short", "z" * 64, "A" * 64):
        with pytest.raises(RatingValidationError):
            await service.list(bad)
    with pytest.raises(RatingValidationError):
        await service.delete(good, "nope")


async def test_forget_text_also_deletes_the_query_log_rows():
    repository = MagicMock()
    repository.forget_text = AsyncMock(return_value=2)
    history = MagicMock()
    history.delete_by_query_hash = AsyncMock(return_value=1)
    service = MemeTextRatingService(repository, 600, history)
    await service.forget_text("a" * 64)
    repository.forget_text.assert_awaited_once_with("a" * 64)
    history.delete_by_query_hash.assert_awaited_once_with("a" * 64)


async def test_forget_text_without_a_history_repository_still_works():
    repository = MagicMock()
    repository.forget_text = AsyncMock(return_value=0)
    await MemeTextRatingService(repository, 600).forget_text("a" * 64)
    repository.forget_text.assert_awaited_once()
