"""
Integration tests for batch/build_meme_profiles.py (candidate collection and the whole _process loop with a
fake extractor). Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import types
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

import batch.build_meme_profiles as module
from ai.meme_profile_extractor import ExtractionResult, ProfileExtractionError
from ai.meme_profile_prompt import PROMPT_VERSION
from batch.utils.meme_profile_bundle import BundleLimits
from batch.utils.meme_profile_config import PIPELINE, MemeProfilesConfig
from repository.image_procesing_status import ImageProcessingStatusRepository
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import (
    DescriptionNote, Image, ImageDescription, ImageDescriptionFeedback, ImageProcessingStatus, MemeProfile, OCRText,
)
from taxonomy import load as load_taxonomy

CONFIG = MemeProfilesConfig(
    text_model="text-model", vision_model="vision-model", num_ctx=2048, batch_size=2, concurrent_requests=1,
    min_text_chars=20, seed=1, ocr_confidence_min=0.4, ocr_lang_score_min=0.3,
    limits=BundleLimits(note_chars=200, description_chars=200, ocr_chars=200))
LONG_TEXT = "a cat looking betrayed by a closed door"


async def _insert_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


async def _add_description(session, image, text=LONG_TEXT, prompt_key="general_description"):
    description = ImageDescription(image_id=image.id, prompt_key=prompt_key, model_used="m", text=text)
    session.add(description)
    await session.flush()
    return description


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


def _fake_extractor_class(calls, fail_for=()):
    class FakeExtractor:
        def __init__(self, tax, *, seed, num_ctx):
            pass

        def extract(self, *, model, note, descriptions, ocr, image_path=None):
            calls.append({"model": model, "descriptions": descriptions, "image_path": image_path})
            if any(text in (ocr or "") + "".join(t for _, t in descriptions) for text in fail_for):
                raise ProfileExtractionError("unusable answer")
            profile = {"situation": "plans cancelled", "situationConfidence": None,
                       "emotion": [{"key": "resignation", "confidence": 0.9, "source": "model"}],
                       "stance": [], "topic": [], "tone": []}
            return ExtractionResult(profile=profile, use_when="Use when plans fall through.")

    return FakeExtractor


def _stub_settings():
    """What _process reads from `settings`: BASE_PATH, GENERAL.PROGRESS_EVERY and `get` (load_config is patched)."""
    return types.SimpleNamespace(BASE_PATH="/tmp/test_images", GENERAL=types.SimpleNamespace(PROGRESS_EVERY=10),
                                 get=lambda *args, **kwargs: None)


async def _run(db_session, calls, *, fail_for=(), extractor_class=None, **kwargs):
    params = dict(reset=False, limit=None, retry_failed=False)
    params.update(kwargs)
    with patch.object(module, "load_config", return_value=CONFIG), \
         patch.object(module, "AsyncSessionLocal", _SessionFactory(db_session)), \
         patch.object(module, "OllamaProfileExtractor", extractor_class or _fake_extractor_class(calls, fail_for)), \
         patch.object(module, "settings", _stub_settings()):
        return await module._process(**params)


@pytest.mark.asyncio(loop_scope="session")
async def test_collect_candidates_builds_bundles_and_excludes_rejected_descriptions(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image, text="kept description", prompt_key="a")
    rejected = await _add_description(db_session, image, text="rejected description", prompt_key="b")
    db_session.add(ImageDescriptionFeedback(image_description_id=rejected.id, approved=False))
    db_session.add(DescriptionNote(image_id=image.id, text="owner note"))
    db_session.add(OCRText(image_id=image.id, text="HELLO", confidence=0.9, lang_score=0.9, language="en"))
    await db_session.flush()

    candidates = await module._collect_candidates(db_session, CONFIG)

    candidate = next(c for c in candidates if c.image_id == image.id)
    assert candidate.bundle.note == "owner note"
    assert candidate.bundle.descriptions == (("a", "kept description"),)
    assert candidate.bundle.ocr == "HELLO"
    assert len(candidate.fingerprint) == 64


@pytest.mark.asyncio(loop_scope="session")
async def test_process_saves_profiles_runs_text_before_vision_and_reports_stats(db_session):
    text_image = await _insert_image(db_session)
    await _add_description(db_session, text_image)
    vision_image = await _insert_image(db_session)  # no text at all -> vision path
    calls: list = []

    stats = await _run(db_session, calls)

    assert [call["model"] for call in calls] == ["text-model", "vision-model"]
    assert calls[0]["image_path"] is None
    assert calls[1]["image_path"].endswith(vision_image.filename)
    assert stats["saved"] == 2 and stats["path.text"] == 1 and stats["path.vision"] == 1
    assert stats["images_selected"] == 2 and stats["images_remaining"] == 0
    assert stats["coverage"]["profiled"] == 2 and stats["coverage"]["stale"] == 0
    row = await db_session.get(MemeProfile, text_image.id)
    assert (row.extractor_model, row.extractor_prompt_version) == ("text-model", PROMPT_VERSION)
    assert row.taxonomy_version == load_taxonomy().version


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_run_with_unchanged_inputs_does_nothing(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image)
    await _run(db_session, [])

    calls: list = []
    stats = await _run(db_session, calls)

    assert calls == []
    assert stats["images_selected"] == 0 and stats.get("saved", 0) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_rejecting_a_description_changes_the_fingerprint_and_re_extracts(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image, text="first description text", prompt_key="a")
    second = await _add_description(db_session, image, text="second description text", prompt_key="b")
    await _run(db_session, [])

    db_session.add(ImageDescriptionFeedback(image_description_id=second.id, approved=False))
    await db_session.flush()
    calls: list = []
    stats = await _run(db_session, calls)

    assert stats["saved"] == 1
    assert calls[0]["descriptions"] == [("a", "first description text")]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_image_is_recorded_skipped_next_time_and_retried_on_request(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image, text=LONG_TEXT + " POISON")

    stats = await _run(db_session, [], fail_for=("POISON",))
    assert stats["error.invalid_output"] == 1 and stats.get("saved", 0) == 0
    status = (await db_session.execute(
        select(ImageProcessingStatus.pipeline, ImageProcessingStatus.status)
        .where(ImageProcessingStatus.image_id == image.id))).one()
    assert tuple(status) == (PIPELINE, "failed")
    assert await db_session.get(MemeProfile, image.id) is None

    skipped = await _run(db_session, [], fail_for=("POISON",))
    assert skipped["skipped.failed"] == 1 and skipped["images_selected"] == 0

    calls: list = []
    retried = await _run(db_session, calls, retry_failed=True)
    assert retried["saved"] == 1 and len(calls) == 1
    done_status = (await db_session.execute(
        select(ImageProcessingStatus.status).where(ImageProcessingStatus.image_id == image.id))).scalar_one()
    assert done_status == "done"  # a Core upsert does not refresh ORM objects, so read the column


@pytest.mark.asyncio(loop_scope="session")
async def test_a_model_error_is_counted_separately_and_does_not_stop_the_run(db_session):
    ok_image = await _insert_image(db_session)
    await _add_description(db_session, ok_image)
    bad_image = await _insert_image(db_session)
    await _add_description(db_session, bad_image, text=LONG_TEXT + " CRASH")

    class Boom(Exception):
        pass

    class Crashing(_fake_extractor_class([])):
        def extract(self, **kwargs):
            if "CRASH" in "".join(t for _, t in kwargs["descriptions"]):
                raise Boom("ollama down")
            return super().extract(**kwargs)

    stats = await _run(db_session, [], extractor_class=Crashing)

    assert stats["saved"] == 1 and stats["error.model"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_human_verified_profiles_are_not_rewritten_and_count_as_needing_review(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(
        image.id,
        ProfileData("human situation", None, "Use when human.",
                    {"emotion": [{"key": "joy", "confidence": 1.0, "source": "human"}]}),
        ProfileMeta(load_taxonomy().version, "text-model", "old-prompt-version", "stale-fingerprint", True))
    await db_session.flush()

    calls: list = []
    stats = await _run(db_session, calls)

    assert calls == []
    assert stats["coverage"]["needs_review"] == 1 and stats["coverage"]["human_verified"] == 1
    row = await db_session.get(MemeProfile, image.id)
    assert row.situation_text == "human situation"


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_caps_the_run_and_reports_what_is_left(db_session):
    for _ in range(3):
        image = await _insert_image(db_session)
        await _add_description(db_session, image)

    stats = await _run(db_session, [], limit=2)

    assert stats["images_selected"] == 2 and stats["images_remaining"] == 1 and stats["saved"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_deletes_model_profiles_and_failures_but_keeps_human_verified(db_session):
    model_image = await _insert_image(db_session)
    await _add_description(db_session, model_image)
    human_image = await _insert_image(db_session)
    await _add_description(db_session, human_image)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(
        human_image.id, ProfileData("kept", None, None, {}),
        ProfileMeta(load_taxonomy().version, "text-model", PROMPT_VERSION, "any", True))
    await ImageProcessingStatusRepository(db_session, PIPELINE).record_failure(model_image.id, "earlier failure")
    await db_session.flush()

    calls: list = []
    await _run(db_session, calls, reset=True)

    assert [call["descriptions"] for call in calls] == [[("general_description", LONG_TEXT)]]
    kept = await db_session.get(MemeProfile, human_image.id)
    assert kept.situation_text == "kept"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unsupported_extension_vision_image_does_not_starve_the_run(db_session):
    webp_image = Image(filename=f"{uuid.uuid4()}.webp")
    db_session.add(webp_image)
    await db_session.flush()
    jpg_image = await _insert_image(db_session)  # neither has any text -> both on the vision path
    calls: list = []

    stats = await _run(db_session, calls, limit=1)

    assert stats["skipped.unsupported_ext"] == 1
    assert stats["images_selected"] == 1 and stats["images_remaining"] == 0 and stats["saved"] == 1
    assert len(calls) == 1
    assert await db_session.get(MemeProfile, jpg_image.id) is not None
    assert await db_session.get(MemeProfile, webp_image.id) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_coverage_reflects_a_stale_profile_the_run_fixed(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image)
    await MemeProfileRepository(db_session).save_profile(
        image.id, ProfileData("old situation", None, "Use when old.", {}),
        ProfileMeta(load_taxonomy().version - 1, "text-model", PROMPT_VERSION, "stale-fingerprint", False))
    await db_session.flush()

    stats = await _run(db_session, [])

    assert stats["saved"] == 1
    assert stats["coverage"]["stale"] == 0 and stats["coverage"]["profiled"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_deletes_and_rebuilds_an_existing_model_profile(db_session):
    image = await _insert_image(db_session)
    await _add_description(db_session, image)
    await _run(db_session, [])
    assert await db_session.get(MemeProfile, image.id) is not None

    calls: list = []
    await _run(db_session, calls, reset=True)

    assert len(calls) == 1

    rebuilt = (await db_session.execute(
        select(MemeProfile.extractor_model).where(MemeProfile.image_id == image.id))).scalar_one_or_none()
    assert rebuilt == "text-model"


@pytest.mark.asyncio(loop_scope="session")
async def test_an_empty_extraction_is_treated_as_failed(db_session):
    """Test that an empty answer (no situation, no use-when, no values) is recorded as failed."""
    image = await _insert_image(db_session)
    await _add_description(db_session, image)

    class EmptyAnswerExtractor(_fake_extractor_class([])):
        def extract(self, *, model, note, descriptions, ocr, image_path=None):
            raise ProfileExtractionError("the answer is empty: no situation, use-when or values")

    stats = await _run(db_session, [], extractor_class=EmptyAnswerExtractor)

    assert stats["error.invalid_output"] == 1 and stats.get("saved", 0) == 0
    status = (await db_session.execute(
        select(ImageProcessingStatus.pipeline, ImageProcessingStatus.status)
        .where(ImageProcessingStatus.image_id == image.id))).one()
    assert tuple(status) == (PIPELINE, "failed")
    assert await db_session.get(MemeProfile, image.id) is None


def _raising_extractor_class(calls, error_for):
    """Fake whose extract raises error_for(description_text, call_index) when that returns an exception."""
    base = _fake_extractor_class([])

    class Raising(base):
        def extract(self, **kwargs):
            index = len(calls)
            calls.append({"model": kwargs["model"], "descriptions": kwargs["descriptions"]})
            error = error_for("".join(t for _, t in kwargs["descriptions"]), index)
            if error is not None:
                raise error
            return super().extract(**kwargs)

    return Raising


async def _status_rows(db_session, image_ids):
    return (await db_session.execute(
        select(ImageProcessingStatus.image_id).where(ImageProcessingStatus.image_id.in_(image_ids)))).all()


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failed_image_is_skipped_again_and_re_extracted_when_its_inputs_change(db_session):
    image = await _insert_image(db_session)
    description = await _add_description(db_session, image, text=LONG_TEXT + " POISON")
    await _run(db_session, [], fail_for=("POISON",))

    skipped = await _run(db_session, [], fail_for=("POISON",))
    assert skipped["skipped.failed"] == 1 and skipped["images_selected"] == 0

    description.text = "a completely different description of the same meme"
    await db_session.flush()
    calls: list = []
    stats = await _run(db_session, calls, fail_for=("POISON",))

    assert len(calls) == 1 and stats["saved"] == 1 and stats.get("skipped.failed", 0) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_repeated_connection_errors_abort_without_recording_failures(db_session):
    images = []
    for _ in range(5):
        image = await _insert_image(db_session)
        await _add_description(db_session, image)
        images.append(image)
    calls: list = []
    extractor = _raising_extractor_class(calls, lambda text, index: ConnectionError("ollama down"))

    with pytest.raises(RuntimeError, match="unavailable"):
        await _run(db_session, [], extractor_class=extractor)

    ids = [image.id for image in images]
    assert len(calls) == 3
    assert await _status_rows(db_session, ids) == []
    assert (await db_session.execute(select(MemeProfile.image_id).where(MemeProfile.image_id.in_(ids)))).all() == []


@pytest.mark.asyncio(loop_scope="session")
async def test_a_single_connection_error_does_not_abort_or_record_a_failure(db_session):
    images = []
    for _ in range(3):
        image = await _insert_image(db_session)
        await _add_description(db_session, image)
        images.append(image)
    extractor = _raising_extractor_class([], lambda text, index: ConnectionError("blip") if index == 0 else None)

    stats = await _run(db_session, [], extractor_class=extractor)

    assert stats["error.unavailable"] == 1 and stats["saved"] == 2
    ids = [image.id for image in images]
    profiled = {row[0] for row in (await db_session.execute(
        select(MemeProfile.image_id).where(MemeProfile.image_id.in_(ids)))).all()}
    unprofiled = [i for i in ids if i not in profiled]
    assert len(unprofiled) == 1
    assert await _status_rows(db_session, unprofiled) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_response_error_status_decides_between_run_level_and_per_image(db_session):
    import ollama

    missing = await _insert_image(db_session)
    await _add_description(db_session, missing, text=LONG_TEXT + " MISSINGMODEL")
    bad = await _insert_image(db_session)
    await _add_description(db_session, bad, text=LONG_TEXT + " BADIMAGE")

    def error_for(text, index):
        if "MISSINGMODEL" in text:
            return ollama.ResponseError("model not found", 404)
        if "BADIMAGE" in text:
            return ollama.ResponseError("Failed to load image", 400)
        return None

    stats = await _run(db_session, [], extractor_class=_raising_extractor_class([], error_for))

    assert stats["error.unavailable"] == 1 and stats["error.model"] == 1
    assert await _status_rows(db_session, [missing.id]) == []
    message = (await db_session.execute(
        select(ImageProcessingStatus.error_message).where(ImageProcessingStatus.image_id == bad.id))).scalar_one()
    assert message.startswith("[") and "Failed to load image" in message


@pytest.mark.asyncio(loop_scope="session")
async def test_only_ids_restricts_selection_to_the_listed_images(db_session):
    listed = await _insert_image(db_session)
    await _add_description(db_session, listed)
    unlisted = await _insert_image(db_session)
    await _add_description(db_session, unlisted)
    calls: list = []

    stats = await _run(db_session, calls, only_ids=frozenset({listed.id}))

    assert stats["saved"] == 1 and len(calls) == 1
    assert await db_session.get(MemeProfile, listed.id) is not None
    assert await db_session.get(MemeProfile, unlisted.id) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_with_only_ids_deletes_only_the_listed_images_profiles_and_failures(db_session):
    listed = await _insert_image(db_session)
    await _add_description(db_session, listed)
    other = await _insert_image(db_session)
    await _add_description(db_session, other)
    await _run(db_session, [])                                   # profiles for both
    status_repo = ImageProcessingStatusRepository(db_session, PIPELINE)
    await status_repo.record_failure(listed.id, "old failure")
    await status_repo.record_failure(other.id, "old failure")
    await db_session.flush()

    calls: list = []
    await _run(db_session, calls, reset=True, only_ids=frozenset({listed.id}))

    assert len(calls) == 1                                       # only the listed image was re-extracted
    assert await db_session.get(MemeProfile, other.id) is not None
    failed = await status_repo.get_image_ids_with_status("failed")
    assert other.id in failed and listed.id not in failed
