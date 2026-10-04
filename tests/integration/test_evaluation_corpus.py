"""
Integration tests for the evaluation corpus builder and profile-state report. Requires a live PostgreSQL
instance with pgvector -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from batch.utils.meme_profile_bundle import BundleLimits
from batch.utils.meme_profile_config import MemeProfilesConfig
from evaluation.corpus import load_candidates
from evaluation.state import collect_profile_state
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageDescription, ImageExtras, OCRText, TmpImageClusters,
)

CONFIG = MemeProfilesConfig(
    text_model="t", vision_model="v", num_ctx=1, batch_size=1, concurrent_requests=1, min_text_chars=20, seed=1,
    ocr_confidence_min=0.4, ocr_lang_score_min=0.3, limits=BundleLimits(200, 200, 200))
LONG = "a cat looking betrayed by a closed door"


async def _image(session, filename=None) -> Image:
    image = Image(filename=filename or f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image


async def _with_text(session, image, *, description=True, ocr=True):
    if description:
        session.add(ImageDescription(image_id=image.id, prompt_key="p", model_used="m", text=LONG))
    if ocr:
        session.add(OCRText(image_id=image.id, text=LONG, confidence=0.9, lang_score=0.9, language="en"))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_candidates_carry_strata_hashes_and_exclude_flagged_unsupported_and_inactive(db_session):
    both = await _image(db_session)
    await _with_text(db_session, both)
    ocr_only = await _image(db_session)
    await _with_text(db_session, ocr_only, description=False)
    description_only = await _image(db_session)
    await _with_text(db_session, description_only, ocr=False)
    no_text = await _image(db_session)
    flagged = await _image(db_session)
    await _with_text(db_session, flagged)
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    gif = await _image(db_session, filename=f"{uuid.uuid4()}.gif")
    await _with_text(db_session, gif)
    pending = Image(filename=f"{uuid.uuid4()}.jpg", status="pending")
    db_session.add(pending)
    await db_session.flush()
    both.content_hash = "hash-both"
    await db_session.flush()

    candidates = {c.image_id: c for c in await load_candidates(db_session, CONFIG)}

    assert candidates[str(both.id)].stratum == "ocr+description" and candidates[str(both.id)].content_hash == "hash-both"
    assert candidates[str(ocr_only.id)].stratum == "ocr only"
    assert candidates[str(description_only.id)].stratum == "description only"
    assert candidates[str(no_text.id)].stratum == "no usable text"
    for excluded in (flagged, gif, pending):
        assert str(excluded.id) not in candidates


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_cluster_members_share_a_cluster_key_and_singletons_get_their_own(db_session):
    first, second, single = await _image(db_session), await _image(db_session), await _image(db_session)
    db_session.add_all([TmpImageClusters(cluster_id=900001, image_id=first.id),
                        TmpImageClusters(cluster_id=900001, image_id=second.id)])
    await db_session.flush()

    candidates = {c.image_id: c for c in await load_candidates(db_session, CONFIG)}

    assert candidates[str(first.id)].cluster_key == candidates[str(second.id)].cluster_key == "c900001"
    assert candidates[str(single.id)].cluster_key == f"s{single.id}"


@pytest.mark.asyncio(loop_scope="session")
async def test_profile_state_counts_versions_models_and_embeddings_for_the_corpus_only(db_session):
    in_corpus, also_in, outside = await _image(db_session), await _image(db_session), await _image(db_session)
    repo = MemeProfileRepository(db_session)
    data = ProfileData("s", None, None, {})
    await repo.save_profile(in_corpus.id, data, ProfileMeta(2, "text-model", "p2-a", "f" * 64, False))
    await repo.save_profile(also_in.id, data, ProfileMeta(2, "vision-model", "p2-a", "f" * 64, False))
    await repo.save_profile(outside.id, data, ProfileMeta(1, "old-model", "p2-old", "f" * 64, False))
    await repo.save_situation_embedding(in_corpus.id, [0.1] * PROFILE_EMBEDDING_DIM, "bge-large", "digest-1")
    await db_session.flush()

    state = await collect_profile_state(db_session, [str(in_corpus.id), str(also_in.id), str(uuid.uuid4())])

    assert state == {
        "corpusImages": 3, "profiled": 2, "taxonomyVersions": {"2": 2}, "promptVersions": {"p2-a": 2},
        "extractorModels": {"text-model": 1, "vision-model": 1}, "embedded": 1,
        "embeddingModels": {"bge-large digest-1": 1},
    }
