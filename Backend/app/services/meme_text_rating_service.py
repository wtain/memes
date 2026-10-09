"""Ratings of meme-from-text results (P5b): validation, hashing and storage rules."""
import re
import uuid

from Backend.app.types.generated.ratingrequest import Schema as RatingRequest
from Backend.app.types.generated.ratingresponse import Schema as RatingResponse
from Backend.app.types.generated.ratingsresponse import Schema as RatingsResponse
from retrieval.query_hash import normalised_query, query_hash

HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RatingValidationError(ValueError):
    """The request is well-formed JSON but not acceptable. The message never contains the user's text."""


class UnknownImageError(LookupError):
    """The image does not exist or is not active."""


def _image_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise RatingValidationError("imageId is not a valid id") from None


def _hash(value: str) -> str:
    if not isinstance(value, str) or not HASH_PATTERN.match(value):
        raise RatingValidationError("queryHash must be 64 lowercase hex characters")
    return value


class MemeTextRatingService:
    def __init__(self, repository, max_chars: int):
        self.repository = repository
        self.max_chars = max_chars

    async def put(self, request: RatingRequest) -> RatingResponse:
        text = normalised_query(request.text, self.max_chars)
        if not text:
            raise RatingValidationError("the message is empty")
        image_id = _image_id(request.imageId)
        if not await self.repository.image_is_active(image_id):
            raise UnknownImageError()
        digest = query_hash(text)
        grade = await self.repository.upsert(
            query_hash=digest, query_text=text, image_id=image_id, grade=request.grade, rank=request.rank,
            reading=request.reading.value if hasattr(request.reading, "value") else request.reading,
            parser_version=request.parserVersion, taxonomy_version=request.taxonomyVersion,
            degraded=request.degraded, fallback=request.fallback,
            language=request.language.value if hasattr(request.language, "value") else request.language)
        return RatingResponse(queryHash=digest, imageId=str(image_id), grade=grade)

    async def delete(self, hash_value: str, image_id: str) -> None:
        await self.repository.delete(_hash(hash_value), _image_id(image_id))

    async def list(self, hash_value: str) -> RatingsResponse:
        digest = _hash(hash_value)
        grades = await self.repository.grades_for(digest)
        return RatingsResponse.model_validate(
            {"queryHash": digest, "ratings": [{"imageId": str(image_id), "grade": grade} for image_id, grade in grades]})

    async def forget_text(self, hash_value: str) -> None:
        await self.repository.forget_text(_hash(hash_value))
