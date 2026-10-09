"""Integration tests for the curator additions of repository/meme_profiles.py (P5c). Needs the test database."""
import uuid

import pytest
from sqlalchemy import select

from repository import meme_profiles
from repository.meme_profiles import CATEGORIES, PIPELINE, MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageProcessingStatus, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue,
)

CURRENT = 2
MODEL = "bge-large"


async def _image(session, status="active") -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


def _meta(**overrides) -> ProfileMeta:
    values = dict(taxonomy_version=CURRENT, extractor_model="qwen2", extractor_prompt_version="p2-test",
                  inputs_fingerprint="f" * 64, human_verified=False)
    values.update(overrides)
    return ProfileMeta(**values)


def _data(situation="plans cancelled", **axes) -> ProfileData:
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    return ProfileData(situation_text=situation, situation_confidence=None, use_when="Use when plans fall through.",
                       values=values)


async def _vector(repo, image_id, model=MODEL):
    await repo.save_situation_embedding(image_id, [0.1] * PROFILE_EMBEDDING_DIM, model, "digest")


def test_the_pipeline_name_matches_the_batch_job():
    from batch.utils.meme_profile_config import PIPELINE as BATCH_PIPELINE
    assert PIPELINE == BATCH_PIPELINE


@pytest.mark.asyncio(loop_scope="session")
async def test_get_view_returns_all_four_axes_ordered_by_confidence(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("pride", 0.4), ("joy", 0.9), ("amusement", 0.9)]), _meta())
    await db_session.flush()

    view = await repo.get_view(image.id)

    assert set(view.values) == {"emotion", "stance", "topic", "tone"}
    assert [item["key"] for item in view.values["emotion"]] == ["amusement", "joy", "pride"]
    assert view.values["stance"] == [] and view.human_verified is False and view.taxonomy_version == CURRENT
    assert view.situation_text == "plans cancelled" and isinstance(view.extracted_at, str)
    assert await repo.get_view(uuid.uuid4()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_image_is_active_only_for_active_images(db_session):
    active, pending = await _image(db_session), await _image(db_session, status="pending")
    repo = MemeProfileRepository(db_session)
    assert await repo.image_is_active(active.id) is True
    assert await repo.image_is_active(pending.id) is False
    assert await repo.image_is_active(uuid.uuid4()) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_vector_status_covers_none_pending_and_fresh(db_session):
    blank, with_text = await _image(db_session), await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(blank.id, _data(situation=None), _meta())
    await repo.save_profile(with_text.id, _data(), _meta())
    await db_session.flush()

    assert await repo.vector_status(uuid.uuid4(), MODEL) == "none"
    assert await repo.vector_status(blank.id, MODEL) == "none"
    assert await repo.vector_status(with_text.id, MODEL) == "pending"
    await _vector(repo, with_text.id)
    await db_session.flush()
    assert await repo.vector_status(with_text.id, MODEL) == "fresh"
    assert await repo.vector_status(with_text.id, "another-model") == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_confirm_marks_verified_and_keeps_values_sources_and_vector(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("joy", 0.9)]), _meta(taxonomy_version=1))
    await _vector(repo, image.id)
    await db_session.flush()

    assert await repo.confirm(image.id, CURRENT) is True
    await db_session.flush()

    view = await repo.get_view(image.id)
    assert view.human_verified is True and view.taxonomy_version == CURRENT
    assert view.values["emotion"] == [{"key": "joy", "confidence": 0.9, "source": "model"}]
    assert await repo.vector_status(image.id, MODEL) == "fresh"
    assert await repo.confirm(uuid.uuid4(), CURRENT) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_deletes_the_profile_values_vector_and_only_this_pipelines_failure(db_session):
    image, other = await _image(db_session), await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(image.id, _data(emotion=[("joy", 1.0)]), _meta(human_verified=True))
    await repo.save_profile(other.id, _data(emotion=[("pride", 1.0)]), _meta())
    await _vector(repo, image.id)
    db_session.add_all([
        ImageProcessingStatus(image_id=image.id, pipeline=PIPELINE, status="failed", error_message="x"),
        ImageProcessingStatus(image_id=image.id, pipeline="other_pipeline", status="done"),
    ])
    await db_session.flush()

    image_id, other_id = image.id, other.id   # read before expire_all: an expired attribute would lazy-load
    assert await repo.reset(image_id) is True
    await db_session.flush()
    db_session.expire_all()

    assert await repo.get_view(image_id) is None
    assert (await db_session.execute(select(MemeProfileValue).where(MemeProfileValue.image_id == image_id))).first() is None
    assert (await db_session.execute(
        select(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image_id))).first() is None
    left = (await db_session.execute(
        select(ImageProcessingStatus.pipeline).where(ImageProcessingStatus.image_id == image_id))).scalars().all()
    assert left == ["other_pipeline"]
    assert await repo.get_view(other_id) is not None
    assert await repo.reset(image_id) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_saving_a_human_profile_keeps_the_vector_only_when_the_situation_is_unchanged(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(situation="plans cancelled"), _meta())
    await _vector(repo, image.id)
    await db_session.flush()

    await repo.save_human_profile(image.id, _data(situation="plans cancelled", emotion=[("joy", 1.0)]), _meta(human_verified=True))
    await db_session.flush()
    assert await repo.vector_status(image.id, MODEL) == "fresh"

    await repo.save_human_profile(image.id, _data(situation="a different situation"), _meta(human_verified=True))
    await db_session.flush()
    assert await repo.vector_status(image.id, MODEL) == "pending"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_batch_write_cannot_overwrite_a_human_save(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(image.id, _data(emotion=[("joy", 1.0)]), _meta(human_verified=True))
    await db_session.flush()

    assert await repo.save_profile(image.id, _data(emotion=[("pride", 0.5)]), _meta()) is False
    assert (await repo.get_view(image.id)).values["emotion"][0]["key"] == "joy"


async def _corpus(session):
    """One image per category, plus decoys that must be counted nowhere (a pending image)."""
    repo = MemeProfileRepository(session)
    rows = {name: await _image(session) for name in
            ("without", "fresh", "stale", "human_stale", "human_fresh", "failed", "no_text")}
    await _image(session, status="pending")                                   # not active: counted nowhere
    await repo.save_profile(rows["fresh"].id, _data(), _meta())
    await _vector(repo, rows["fresh"].id)
    await repo.save_profile(rows["stale"].id, _data(), _meta(taxonomy_version=1))
    await repo.save_human_profile(rows["human_stale"].id, _data(), _meta(taxonomy_version=1, human_verified=True))
    await _vector(repo, rows["human_stale"].id)
    await repo.save_human_profile(rows["human_fresh"].id, _data(), _meta(human_verified=True))
    await _vector(repo, rows["human_fresh"].id)
    await repo.save_profile(rows["no_text"].id, _data(situation=None), _meta())
    session.add(ImageProcessingStatus(image_id=rows["failed"].id, pipeline=PIPELINE, status="failed", error_message="x"))
    await session.flush()
    return repo, rows


@pytest.mark.asyncio(loop_scope="session")
async def test_live_counts_and_category_lists_agree(db_session):
    repo, rows = await _corpus(db_session)

    counts = await repo.live_counts(CURRENT, MODEL)

    assert counts == {"active": 7, "profiled": 5, "without_profile": 2, "needs_review": 1, "stale_taxonomy": 1,
                      "failed": 1, "human_verified": 2, "pending_vector": 1}
    expected = {
        "without_profile": {rows["without"].id, rows["failed"].id},
        "needs_review": {rows["human_stale"].id},
        "stale_taxonomy": {rows["stale"].id},
        "failed": {rows["failed"].id},
        "human_verified": {rows["human_stale"].id, rows["human_fresh"].id},
        "pending_vector": {rows["stale"].id},
    }
    for category in CATEGORIES:
        listed, total = await repo.list_category(category, CURRENT, MODEL, None, 100)
        assert total == counts[category], category
        assert {image_id for image_id, _ in listed} == expected[category], category
    listed, _ = await repo.list_category("human_verified", CURRENT, MODEL, None, 100)
    assert all(human for _, human in listed)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_vector_from_another_model_counts_as_pending(db_session):
    image = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta())
    await _vector(repo, image.id, model="old-model")
    await db_session.flush()

    listed, total = await repo.list_category("pending_vector", CURRENT, MODEL, None, 10)

    assert total == 1 and [image_id for image_id, _ in listed] == [image.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_list_category_pages_by_image_id(db_session):
    repo = MemeProfileRepository(db_session)
    created = [await _image(db_session) for _ in range(5)]

    first, total = await repo.list_category("without_profile", CURRENT, MODEL, None, 2)
    second, _ = await repo.list_category("without_profile", CURRENT, MODEL, first[-1][0], 2)
    last, _ = await repo.list_category("without_profile", CURRENT, MODEL, second[-1][0], 2)

    assert total == 5
    ids = [image_id for image_id, _ in first + second + last]
    assert len(ids) == 5 and ids == sorted(ids, key=str) and set(ids) == {image.id for image in created}


def test_the_repository_never_commits():
    import inspect
    assert "commit(" not in inspect.getsource(meme_profiles)
