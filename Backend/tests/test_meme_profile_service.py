import uuid

import pytest

from Backend.app.services.meme_profile_service import (
    MAX_USE_WHEN_CHARS, MemeProfileService, ProfileConflictError, ProfileValidationError, UnknownImageError,
)
from repository.meme_profiles import ProfileView
from retrieval.embed import EmbedTimeout, EmbedUnavailable
from taxonomy import load

TAX = load()
IMAGE = str(uuid.uuid4())
EMPTY_VALUES = {"emotion": [], "stance": [], "topic": [], "tone": []}


class FakeRepo:
    def __init__(self, view=None, active=True, vector="pending"):
        self.view, self.active, self.vector = view, active, vector
        self.saved, self.vectors, self.confirmed, self.reset_calls = [], [], [], []

    async def image_is_active(self, image_id):
        return self.active

    async def get_view(self, image_id):
        return self.view

    async def vector_status(self, image_id, model_name):
        return self.vector

    async def save_human_profile(self, image_id, data, meta):
        self.saved.append((data, meta))
        values = {axis: [{"key": i["key"], "confidence": 1.0, "source": "human"} for i in items]
                  for axis, items in data.values.items()}
        self.view = ProfileView(image_id, meta.taxonomy_version, True, data.situation_text, None, data.use_when,
                                values, "2026-10-09T10:00:00", "2026-10-09T10:00:00")

    async def save_situation_embedding(self, image_id, vector, model_name, model_digest):
        self.vectors.append((vector, model_name, model_digest))
        self.vector = "fresh"

    async def confirm(self, image_id, taxonomy_version):
        self.confirmed.append(taxonomy_version)
        return True

    async def reset(self, image_id):
        self.reset_calls.append(image_id)
        return True


class FakeEmbedder:
    def __init__(self, error=None, digest_error=None):
        self.error, self.digest_error, self.embedded, self.digests = error, digest_error, [], []

    def model_digest(self, model, timeout):
        self.digests.append(model)
        if self.digest_error:
            raise self.digest_error
        return "digest-1"

    def embed(self, model, texts, timeout):
        self.embedded.append(list(texts))
        if self.error:
            raise self.error
        return [[0.5] * 1024 for _ in texts]


def _view(**overrides):
    values = dict(image_id=uuid.UUID(IMAGE), taxonomy_version=TAX.version, human_verified=False,
                  situation_text="old situation", situation_confidence=None, use_when=None,
                  values={**EMPTY_VALUES, "emotion": [{"key": "joy", "confidence": 0.9, "source": "model"}]},
                  extracted_at="2026-10-09T09:00:00", updated_at="2026-10-09T09:00:00")
    values.update(overrides)
    return ProfileView(**values)


def _request(**overrides):
    body = {"situation": "a new situation", "useWhen": "Use when it fits.",
            "values": {"emotion": ["joy"], "stance": [], "topic": ["work"], "tone": []}}
    body.update(overrides)
    return type("Req", (), {
        "situation": body["situation"], "useWhen": body["useWhen"],
        "values": type("Vals", (), body["values"])()})()


def _service(repo=None, embedder=None, **kwargs):
    repo = repo or FakeRepo()
    embedder = embedder or FakeEmbedder()
    return MemeProfileService(repo, embedder, embedding_model="bge-large", embed_timeout=5.0, taxonomy=TAX), repo, embedder


async def test_save_writes_a_human_profile_and_stores_the_fresh_vector():
    service, repo, embedder = _service()

    response = await service.save(IMAGE, _request())

    data, meta = repo.saved[0]
    assert (meta.human_verified, meta.extractor_model, meta.taxonomy_version) == (True, "human", TAX.version)
    assert data.situation_text == "a new situation" and data.use_when == "Use when it fits."
    assert [(i["key"], i["confidence"], i["source"]) for i in data.values["topic"]] == [("work", 1.0, "human")]
    assert embedder.embedded == [["a new situation"]] and repo.vectors[0][1:] == ("bge-large", "digest-1")
    assert response["vector"] == "fresh" and response["humanVerified"] is True and response["exists"] is True


async def test_an_unchanged_situation_makes_no_embedding_call():
    service, repo, embedder = _service(FakeRepo(view=_view(situation_text="a new situation"), vector="fresh"))

    response = await service.save(IMAGE, _request())

    assert embedder.embedded == [] and repo.vectors == [] and response["vector"] == "fresh"


@pytest.mark.parametrize("error", [EmbedTimeout("t"), EmbedUnavailable("u"), RuntimeError("boom")])
async def test_a_failed_embedding_still_saves_and_reports_pending(error):
    service, repo, _ = _service(embedder=FakeEmbedder(error=error))

    response = await service.save(IMAGE, _request())

    assert len(repo.saved) == 1 and repo.vectors == [] and response["vector"] == "pending"


async def test_a_digest_failure_also_degrades_to_pending():
    service, repo, _ = _service(embedder=FakeEmbedder(digest_error=EmbedUnavailable("u")))
    assert (await service.save(IMAGE, _request()))["vector"] == "pending" and repo.vectors == []


async def test_a_blank_situation_has_no_vector_and_no_call():
    service, repo, embedder = _service(FakeRepo(vector="none"))
    response = await service.save(IMAGE, _request(situation="   "))
    assert embedder.embedded == [] and response["vector"] == "none" and repo.saved[0][0].situation_text is None


@pytest.mark.parametrize("bad", [
    dict(values={"emotion": ["not-a-key"], "stance": [], "topic": [], "tone": []}),
    dict(values={"emotion": ["joy", "amusement", "pride", "relief"], "stance": [], "topic": [], "tone": []}),
    dict(situation="x" * 161),
    dict(useWhen="y" * (MAX_USE_WHEN_CHARS + 1)),
])
async def test_invalid_profiles_are_rejected_before_anything_is_written(bad):
    service, repo, embedder = _service()
    with pytest.raises(ProfileValidationError) as error:
        await service.save(IMAGE, _request(**bad))
    assert repo.saved == [] and embedder.embedded == []
    assert "x" * 20 not in str(error.value) and "y" * 20 not in str(error.value)


async def test_unknown_or_inactive_images_and_bad_ids():
    service, repo, _ = _service(FakeRepo(active=False))
    with pytest.raises(UnknownImageError):
        await service.save(IMAGE, _request())
    with pytest.raises(UnknownImageError):
        await service.get(IMAGE)
    with pytest.raises(ProfileValidationError):
        await service.get("not-a-uuid")


async def test_get_of_an_unprofiled_meme_is_an_empty_profile():
    service, _, _ = _service()
    response = await service.get(IMAGE)
    assert response["exists"] is False and response["values"] == EMPTY_VALUES and response["vector"] == "none"
    assert response["currentTaxonomyVersion"] == TAX.version


async def test_needs_review_only_for_a_human_verified_profile_of_an_older_taxonomy():
    service, _, _ = _service(FakeRepo(view=_view(human_verified=True, taxonomy_version=TAX.version - 1)))
    assert (await service.get(IMAGE))["needsReview"] is True
    service, _, _ = _service(FakeRepo(view=_view(human_verified=False, taxonomy_version=TAX.version - 1)))
    assert (await service.get(IMAGE))["needsReview"] is False


async def test_confirm_marks_verified_with_the_current_taxonomy_version():
    service, repo, _ = _service(FakeRepo(view=_view(taxonomy_version=TAX.version - 1)))
    await service.confirm(IMAGE)
    assert repo.confirmed == [TAX.version]


async def test_confirm_refuses_a_missing_profile_and_a_removed_vocabulary_key():
    service, _, _ = _service()
    with pytest.raises(ProfileConflictError):
        await service.confirm(IMAGE)
    stale = _view(values={**EMPTY_VALUES, "emotion": [{"key": "removed-key", "confidence": 0.9, "source": "model"}]})
    service, repo, _ = _service(FakeRepo(view=stale))
    with pytest.raises(ProfileValidationError):
        await service.confirm(IMAGE)
    assert repo.confirmed == []


async def test_reset_deletes_and_is_idempotent_for_a_missing_profile():
    service, repo, _ = _service()
    await service.reset(IMAGE)
    assert len(repo.reset_calls) == 1


def test_the_use_when_limit_matches_the_extractor():
    from ai.meme_profile_extractor import MAX_USE_WHEN_CHARS as EXTRACTOR_LIMIT
    assert MAX_USE_WHEN_CHARS == EXTRACTOR_LIMIT
