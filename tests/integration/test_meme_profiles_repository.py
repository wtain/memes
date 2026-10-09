"""
Integration tests for repository/meme_profiles.py and the three meme profile tables.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue,
)


async def _insert_image(session, status: str | None = None) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    if status is not None:
        image.status = status
    session.add(image)
    await session.flush()
    return image


def _meta(**overrides) -> ProfileMeta:
    values = dict(taxonomy_version=2, extractor_model="qwen2", extractor_prompt_version="p2-test",
                  inputs_fingerprint="f" * 64, human_verified=False)
    values.update(overrides)
    return ProfileMeta(**values)


def _data(situation="plans cancelled", use_when="Use when plans fall through.", **axes) -> ProfileData:
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    return ProfileData(situation_text=situation, situation_confidence=None, use_when=use_when, values=values)


async def _value_rows(session, image_id):
    result = await session.execute(
        select(MemeProfileValue.axis, MemeProfileValue.value_key, MemeProfileValue.confidence, MemeProfileValue.source)
        .where(MemeProfileValue.image_id == image_id)
        .order_by(MemeProfileValue.axis, MemeProfileValue.value_key)
    )
    return [tuple(row) for row in result.all()]


@pytest.mark.asyncio(loop_scope="session")
async def test_save_profile_creates_profile_and_values(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)

    saved = await repo.save_profile(
        image.id, _data(emotion=[("resignation", 0.9)], stance=[("relate", 0.6)]), _meta())
    await db_session.flush()

    assert saved is True
    row = await db_session.get(MemeProfile, image.id)
    assert (row.taxonomy_version, row.extractor_model, row.extractor_prompt_version) == (2, "qwen2", "p2-test")
    assert row.inputs_fingerprint == "f" * 64
    assert row.situation_text == "plans cancelled"
    assert row.use_when == "Use when plans fall through."
    assert row.human_verified is False
    assert await _value_rows(db_session, image.id) == [
        ("emotion", "resignation", 0.9, "model"), ("stance", "relate", 0.6, "model")]


@pytest.mark.asyncio(loop_scope="session")
async def test_save_profile_replaces_the_previous_values(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("joy", 0.9), ("pride", 0.3)]), _meta())
    await db_session.flush()

    await repo.save_profile(image.id, _data(emotion=[("joy", 0.6)], tone=[("absurd", 0.9)]),
                            _meta(inputs_fingerprint="a" * 64))
    await db_session.flush()

    assert await _value_rows(db_session, image.id) == [
        ("emotion", "joy", 0.6, "model"), ("tone", "absurd", 0.9, "model")]
    row = await db_session.get(MemeProfile, image.id)
    await db_session.refresh(row)
    assert row.inputs_fingerprint == "a" * 64


@pytest.mark.asyncio(loop_scope="session")
async def test_save_profile_refuses_a_human_verified_profile(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(image.id, _data(situation="human wrote this", emotion=[("joy", 1.0)]), _meta())
    await db_session.flush()

    saved = await repo.save_profile(image.id, _data(situation="model overwrite", emotion=[("anger", 0.9)]), _meta())
    await db_session.flush()

    assert saved is False
    row = await db_session.get(MemeProfile, image.id)
    await db_session.refresh(row)
    assert row.situation_text == "human wrote this"
    assert await _value_rows(db_session, image.id) == [("emotion", "joy", 1.0, "human")]


@pytest.mark.asyncio(loop_scope="session")
async def test_save_human_profile_sets_flag_and_forces_human_source(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("anger", 0.9)]), _meta())
    await db_session.flush()

    await repo.save_human_profile(image.id, _data(emotion=[("joy", 0.6)]), _meta())
    await db_session.flush()

    row = await db_session.get(MemeProfile, image.id)
    await db_session.refresh(row)
    assert row.human_verified is True
    assert await _value_rows(db_session, image.id) == [("emotion", "joy", 0.6, "human")]


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_embedding_is_dropped_only_when_the_situation_changes(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(situation="plans cancelled"), _meta())
    await repo.save_situation_embedding(image.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    await repo.save_profile(image.id, _data(situation="plans cancelled", use_when="Different sentence."), _meta())
    await db_session.flush()
    kept = (await db_session.execute(
        select(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image.id))).scalar_one_or_none()
    assert kept is not None

    await repo.save_profile(image.id, _data(situation="a different situation"), _meta())
    await db_session.flush()
    dropped = (await db_session.execute(
        select(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image.id))).scalar_one_or_none()
    assert dropped is None


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_model_generated_keeps_human_verified_profiles(db_session):
    model_image = await _insert_image(db_session)
    human_image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(model_image.id, _data(emotion=[("joy", 0.9)]), _meta())
    await repo.save_human_profile(human_image.id, _data(emotion=[("anger", 1.0)]), _meta())
    await db_session.flush()

    deleted = await repo.delete_model_generated()
    await db_session.flush()

    assert deleted == 1
    assert await db_session.get(MemeProfile, model_image.id) is None
    assert await db_session.get(MemeProfile, human_image.id) is not None
    assert await _value_rows(db_session, model_image.id) == []
    assert await _value_rows(db_session, human_image.id) == [("emotion", "anger", 1.0, "human")]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_meta_returns_version_model_fingerprint_and_flag(db_session):
    image = await _insert_image(db_session)
    other = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta(inputs_fingerprint="c" * 64))
    await db_session.flush()

    metas = await repo.get_meta([image.id, other.id])

    assert set(metas) == {image.id}
    assert metas[image.id] == ProfileMeta(2, "qwen2", "p2-test", "c" * 64, False)


@pytest.mark.asyncio(loop_scope="session")
async def test_coverage_counts_active_profiled_stale_needs_review_and_failed(db_session):
    fresh = await _insert_image(db_session)
    old_version = await _insert_image(db_session)
    human_stale = await _insert_image(db_session)
    changed_inputs = await _insert_image(db_session)
    unprofiled = await _insert_image(db_session)
    await _insert_image(db_session, status="pending")
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(fresh.id, _data(), _meta(extractor_prompt_version="p2-now"))
    await repo.save_profile(old_version.id, _data(), _meta(taxonomy_version=1, extractor_prompt_version="p2-now"))
    await repo.save_human_profile(human_stale.id, _data(), _meta(taxonomy_version=1, extractor_prompt_version="p2-now"))
    await repo.save_profile(changed_inputs.id, _data(), _meta(extractor_prompt_version="p2-now"))
    await db_session.flush()

    coverage = await repo.coverage(
        taxonomy_version=2, prompt_version="p2-now",
        stale_image_ids=frozenset({changed_inputs.id}), failed_image_ids=frozenset({unprofiled.id}))

    assert coverage == {"active": 5, "profiled": 4, "stale": 3, "needs_review": 1,
                        "human_verified": 1, "failed": 1}


@pytest.mark.asyncio(loop_scope="session")
async def test_coverage_counts_a_human_profile_stale_only_for_the_taxonomy(db_session):
    human_other_prompt = await _insert_image(db_session)
    human_old_taxonomy = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_human_profile(human_other_prompt.id, _data(), _meta(extractor_prompt_version="human"))
    await repo.save_human_profile(human_old_taxonomy.id, _data(), _meta(taxonomy_version=1, extractor_prompt_version="human"))
    await db_session.flush()

    coverage = await repo.coverage(taxonomy_version=2, prompt_version="p2-now")

    assert (coverage["stale"], coverage["needs_review"], coverage["human_verified"]) == (1, 1, 2)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_profiles_needing_embedding_tracks_model_and_digest(db_session):
    with_situation = await _insert_image(db_session)
    no_situation = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(with_situation.id, _data(situation="plans cancelled"), _meta())
    await repo.save_profile(no_situation.id, _data(situation=None), _meta())
    await db_session.flush()

    assert await repo.get_profiles_needing_embedding("bge-large", "digest-1") == [(with_situation.id, "plans cancelled")]

    await repo.save_situation_embedding(with_situation.id, [0.2] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()
    assert await repo.get_profiles_needing_embedding("bge-large", "digest-1") == []
    assert await repo.get_profiles_needing_embedding("bge-large", "digest-2") == [(with_situation.id, "plans cancelled")]
    assert await repo.get_profiles_needing_embedding("other-model", "digest-1") == [(with_situation.id, "plans cancelled")]


@pytest.mark.asyncio(loop_scope="session")
async def test_save_situation_embedding_upserts(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta())
    await repo.save_situation_embedding(image.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await repo.save_situation_embedding(image.id, [0.3] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-2")
    await db_session.flush()

    rows = (await db_session.execute(
        select(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image.id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].model_digest == "digest-2"


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_embeddings_keeps_profiles(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta())
    await repo.save_situation_embedding(image.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    assert await repo.delete_all_embeddings() == 1
    await db_session.flush()

    assert await db_session.get(MemeProfile, image.id) is not None
    assert await repo.get_profiles_needing_embedding("bge-large", "digest-1") == [(image.id, "plans cancelled")]


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_an_image_cascades_to_profile_values_and_embedding(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(emotion=[("joy", 0.9)]), _meta())
    await repo.save_situation_embedding(image.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    await db_session.execute(delete(Image).where(Image.id == image.id))
    await db_session.flush()

    assert await db_session.get(MemeProfile, image.id) is None
    assert await _value_rows(db_session, image.id) == []
    remaining = (await db_session.execute(
        select(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image.id))).scalars().all()
    assert remaining == []


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_check_constraint_rejects_the_free_text_axis(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta())
    await db_session.flush()

    db_session.add(MemeProfileValue(image_id=image.id, axis="situation", value_key="x", confidence=0.5, source="model"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_source_check_constraint_rejects_unknown_sources(db_session):
    image = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(image.id, _data(), _meta())
    await db_session.flush()

    db_session.add(MemeProfileValue(image_id=image.id, axis="emotion", value_key="joy", confidence=0.5, source="robot"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_coverage_failed_counts_only_active_images(db_session):
    active_failed = await _insert_image(db_session)
    pending_failed = await _insert_image(db_session, status="pending")
    repo = MemeProfileRepository(db_session)

    coverage = await repo.coverage(
        taxonomy_version=2, prompt_version="p2-now",
        failed_image_ids=frozenset({active_failed.id, pending_failed.id}))

    assert coverage["failed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_model_generated_can_be_limited_to_given_images(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(first.id, _data(emotion=[("joy", 0.9)]), _meta())
    await repo.save_profile(second.id, _data(emotion=[("anger", 0.9)]), _meta())
    await db_session.flush()

    deleted = await repo.delete_model_generated({first.id})
    await db_session.flush()

    assert deleted == 1
    assert await db_session.get(MemeProfile, first.id) is None
    assert await db_session.get(MemeProfile, second.id) is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_embeddings_can_be_limited_to_given_images(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    for image in (first, second):
        await repo.save_profile(image.id, _data(), _meta())
        await repo.save_situation_embedding(image.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    assert await repo.delete_all_embeddings({first.id}) == 1
    await db_session.flush()

    assert [image_id for image_id, _ in await repo.get_profiles_needing_embedding("bge-large", "digest-1")] == [first.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_profiles_needing_embedding_can_be_limited_to_given_images(db_session):
    first = await _insert_image(db_session)
    second = await _insert_image(db_session)
    repo = MemeProfileRepository(db_session)
    await repo.save_profile(first.id, _data(situation="one"), _meta())
    await repo.save_profile(second.id, _data(situation="two"), _meta())
    await db_session.flush()

    only_first = await repo.get_profiles_needing_embedding("bge-large", "d1", {first.id})
    everything = await repo.get_profiles_needing_embedding("bge-large", "d1")

    assert only_first == [(first.id, "one")]
    assert {image_id for image_id, _ in everything} == {first.id, second.id}
