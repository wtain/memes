"""Curator operations on meme profiles (P5c): validation, human save with an inline vector, confirm, reset."""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from repository.meme_profiles import AXES, ProfileData, ProfileMeta
from taxonomy import load as load_taxonomy
from taxonomy.validate import validate_profile

logger = logging.getLogger(__name__)

HUMAN = "human"
MAX_USE_WHEN_CHARS = 300   # equals ai.meme_profile_extractor.MAX_USE_WHEN_CHARS; a test pins it
LOCK_TIMEOUT_SECONDS = 10   # a curator write waits at most this long behind a batch write of the same image


class ProfileValidationError(ValueError):
    """Not acceptable. Messages are fixed or name an axis or taxonomy key, never free text."""


class UnknownImageError(LookupError):
    """The image does not exist or is not active."""


class ProfileConflictError(Exception):
    """The operation does not apply to the profile's current state."""


@dataclass(frozen=True)
class _Vector:
    values: list[float]
    digest: str


def _image_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ProfileValidationError("imageId is not a valid id") from None


class MemeProfileService:
    def __init__(self, repository, embedder, *, embedding_model: str, embed_timeout: float, taxonomy=None):
        self.repo = repository
        self.embedder = embedder
        self.model = embedding_model
        self.timeout = embed_timeout
        self.tax = taxonomy or load_taxonomy()

    async def get(self, image_id: str) -> dict:
        image = await self._active(image_id)
        return await self._response(image, await self.repo.get_view(image))

    async def save(self, image_id: str, request) -> dict:
        await self.repo.set_lock_timeout(LOCK_TIMEOUT_SECONDS)
        image = await self._active(image_id)
        data = self._validated(request)
        old = await self.repo.get_view(image)
        vector = None
        if data.situation_text and (old is None or old.situation_text != data.situation_text):
            # Computed BEFORE the write: no lock is held during the Ollama call, and a failure only means "pending".
            vector = await self._embed(image, data.situation_text)
        await self.repo.save_human_profile(
            image, data, ProfileMeta(self.tax.version, HUMAN, HUMAN, HUMAN, True))
        await self.repo.clear_failure(image)
        if vector is not None:
            await self.repo.save_situation_embedding(image, vector.values, self.model, vector.digest)
        return await self._response(image, await self.repo.get_view(image))

    async def confirm(self, image_id: str) -> dict:
        await self.repo.set_lock_timeout(LOCK_TIMEOUT_SECONDS)
        image = await self._active(image_id)
        view = await self.repo.get_view(image)
        if view is None:
            raise ProfileConflictError("this meme has no profile to confirm")
        stored = {"situation": view.situation_text, **{axis: list(items) for axis, items in view.values.items()}}
        if validate_profile(stored, self.tax).errors:
            # Same rules as a save, against the current vocabulary; the message never carries profile text.
            raise ProfileValidationError(
                "the profile is no longer valid for the current vocabulary; edit and save it instead")
        await self.repo.confirm(image, self.tax.version)
        await self.repo.clear_failure(image)
        return await self._response(image, await self.repo.get_view(image))

    async def reset(self, image_id: str) -> None:
        await self.repo.set_lock_timeout(LOCK_TIMEOUT_SECONDS)
        image = await self._active(image_id)
        await self.repo.reset(image)

    async def _active(self, image_id: str) -> uuid.UUID:
        image = _image_id(image_id)
        if not await self.repo.image_is_active(image):
            raise UnknownImageError()
        return image

    def _validated(self, request) -> ProfileData:
        profile = {"situation": request.situation}
        for axis in AXES:
            profile[axis] = [{"key": key, "confidence": 1.0, "source": HUMAN} for key in getattr(request.values, axis)]
        result = validate_profile(profile, self.tax)
        errors = list(result.errors)
        use_when = (request.useWhen or "").strip() or None
        if use_when and len(use_when) > MAX_USE_WHEN_CHARS:
            errors.append(f"useWhen: longer than {MAX_USE_WHEN_CHARS} characters")
        if errors:
            raise ProfileValidationError("the profile is not valid: " + "; ".join(errors))
        return ProfileData.from_validated(result.profile, use_when)

    async def _embed(self, image: uuid.UUID, text: str) -> "_Vector | None":
        deadline = time.monotonic() + self.timeout
        try:
            digest = await asyncio.to_thread(self.embedder.model_digest, self.model, max(deadline - time.monotonic(), 0.0))
            vectors = await asyncio.to_thread(self.embedder.embed, self.model, [text], deadline - time.monotonic())
            return _Vector(list(vectors[0]), digest)
        except Exception as error:   # any failure degrades to "pending"; never log the text
            logger.warning("profile vector not refreshed for %s: %s", image, type(error).__name__)
            return None

    async def _response(self, image: uuid.UUID, view) -> dict:
        base = {"imageId": str(image), "currentTaxonomyVersion": self.tax.version}
        if view is None:
            return {**base, "exists": False, "humanVerified": False, "needsReview": False, "taxonomyVersion": None,
                    "situation": None, "situationConfidence": None, "useWhen": None,
                    "values": {axis: [] for axis in AXES}, "vector": "none", "extractedAt": None, "updatedAt": None}
        return {**base, "exists": True, "humanVerified": view.human_verified,
                "needsReview": view.human_verified and view.taxonomy_version != self.tax.version,
                "taxonomyVersion": view.taxonomy_version, "situation": view.situation_text,
                "situationConfidence": view.situation_confidence, "useWhen": view.use_when, "values": view.values,
                "vector": await self.repo.vector_status(image, self.model),
                "extractedAt": view.extracted_at, "updatedAt": view.updated_at}
