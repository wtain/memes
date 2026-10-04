"""
Integration tests for batch/build_meme_profile_embeddings.py with a fake embedder.
Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
"""
import types
import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import select

import batch.build_meme_profile_embeddings as module
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import PROFILE_EMBEDDING_DIM, Image, MemeProfileSituationEmbedding


async def _profile(session, situation: str | None) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    await MemeProfileRepository(session).save_profile(
        image.id, ProfileData(situation, None, None, {}), ProfileMeta(2, "text-model", "p2-test", "f" * 64, False))
    await session.flush()
    return image


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


def _fake_embedder_class(embedded, digest="digest-1", fail=False):
    class FakeEmbedder:
        def __init__(self, model):
            self.model = model

        def model_digest(self):
            return digest

        def embed(self, texts):
            if fail:
                raise RuntimeError("ollama embed failed")
            embedded.extend(texts)
            vectors = np.zeros((len(texts), PROFILE_EMBEDDING_DIM), dtype=np.float32)
            vectors[:, 0] = 1.0
            return vectors

    return FakeEmbedder


async def _run(db_session, embedder_class, **kwargs):
    params = dict(reset=False, limit=None)
    params.update(kwargs)
    stub_settings = types.SimpleNamespace(
        TAXONOMY=types.SimpleNamespace(EMBEDDING_MODEL="bge-large"), get=lambda *args, **kwargs: None)
    with patch.object(module, "load_config", return_value=MagicMock()), \
         patch.object(module, "AsyncSessionLocal", _SessionFactory(db_session)), \
         patch.object(module, "OllamaEmbedder", embedder_class), \
         patch.object(module, "settings", stub_settings):
        return await module._process(**params)


async def _embeddings(session):
    return (await session.execute(select(MemeProfileSituationEmbedding))).scalars().all()


@pytest.mark.asyncio(loop_scope="session")
async def test_embeds_profiles_with_a_situation_and_skips_the_rest(db_session):
    with_situation = await _profile(db_session, "plans cancelled")
    await _profile(db_session, None)
    embedded: list = []

    stats = await _run(db_session, _fake_embedder_class(embedded))

    assert embedded == ["plans cancelled"]
    assert stats["embedded"] == 1 and stats["images_selected"] == 1 and stats["images_remaining"] == 0
    rows = await _embeddings(db_session)
    assert [(r.image_id, r.model_name, r.model_digest) for r in rows] == [(with_situation.id, "bge-large", "digest-1")]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_run_does_nothing_until_the_model_digest_changes(db_session):
    await _profile(db_session, "plans cancelled")
    await _run(db_session, _fake_embedder_class([]))

    again: list = []
    stats = await _run(db_session, _fake_embedder_class(again))
    assert again == [] and stats["images_selected"] == 0

    new_digest: list = []
    stats = await _run(db_session, _fake_embedder_class(new_digest, digest="digest-2"))
    assert new_digest == ["plans cancelled"] and stats["embedded"] == 1
    assert [r.model_digest for r in await _embeddings(db_session)] == ["digest-2"]


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_caps_the_run_and_reports_the_rest(db_session):
    for index in range(3):
        await _profile(db_session, f"situation {index}")

    stats = await _run(db_session, _fake_embedder_class([]), limit=2)

    assert stats["images_selected"] == 2 and stats["images_remaining"] == 1 and stats["embedded"] == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_deletes_every_embedding_and_re_embeds(db_session):
    await _profile(db_session, "plans cancelled")
    await _run(db_session, _fake_embedder_class([]))

    embedded: list = []
    stats = await _run(db_session, _fake_embedder_class(embedded), reset=True)

    assert embedded == ["plans cancelled"] and stats["embedded"] == 1
    assert len(await _embeddings(db_session)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_an_embedding_error_is_counted_and_nothing_is_saved(db_session):
    await _profile(db_session, "plans cancelled")

    stats = await _run(db_session, _fake_embedder_class([], fail=True))

    assert stats["error.embed"] == 1 and stats.get("embedded", 0) == 0
    assert await _embeddings(db_session) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_only_ids_restricts_embedding_to_the_listed_profiles(db_session):
    listed = await _profile(db_session, "listed situation")
    await _profile(db_session, "unlisted situation")
    embedded: list = []

    stats = await _run(db_session, _fake_embedder_class(embedded), only_ids=frozenset({listed.id}))

    assert embedded == ["listed situation"] and stats["embedded"] == 1
    assert [row.image_id for row in await _embeddings(db_session)] == [listed.id]


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_with_only_ids_deletes_only_the_listed_embeddings(db_session):
    listed = await _profile(db_session, "listed situation")
    other = await _profile(db_session, "other situation")
    await _run(db_session, _fake_embedder_class([]))             # both embedded

    embedded: list = []
    await _run(db_session, _fake_embedder_class(embedded), reset=True, only_ids=frozenset({listed.id}))

    assert embedded == ["listed situation"]
    assert {row.image_id for row in await _embeddings(db_session)} == {listed.id, other.id}
