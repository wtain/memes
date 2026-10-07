"""
Integration tests for repository/meme_retrieval.py. Requires a live PostgreSQL instance with pgvector -- see
tests/integration/conftest.py. No Ollama: query vectors are built by hand.
"""
import inspect
import uuid

import pytest

from repository import meme_retrieval
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from repository.meme_retrieval import MemeRetrievalRepository
from retrieval.model import Filters
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageExtras, ImageTag, OCRLemma, TmpImageClusters,
)

MODEL = "bge-large"
KW = dict(model_name=MODEL, situation_k=50, axis_k=50, lexical_k=50, total_cap=100)


def _vec(*leading):
    vector = [0.0] * PROFILE_EMBEDDING_DIM
    for index, value in enumerate(leading):
        vector[index] = value
    return vector


async def _image(session, *, status="active", flagged=None, filename=None) -> Image:
    image = Image(filename=filename or f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    if flagged is not None:
        session.add(ImageExtras(image_id=image.id, flagged=flagged))
        await session.flush()
    return image


async def _profile(session, image, *, vector=None, model=MODEL, situation="a situation", use_when="Use when it fits.",
                   **axes):
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    repo = MemeProfileRepository(session)
    await repo.save_profile(image.id, ProfileData(situation, None, use_when, values),
                            ProfileMeta(2, "text-model", "p2-test", "f" * 64, False))
    if vector is not None:
        await repo.save_situation_embedding(image.id, vector, model, "digest-1")
    await session.flush()


async def _search(session, **overrides):
    params = dict(query_vector=_vec(1.0), query_axes={}, neighbours=None, lexical_text=None, filters=Filters(),
                  restrict_ids=None)
    params.update(overrides)
    params.update({key: value for key, value in KW.items() if key not in overrides})
    return await MemeRetrievalRepository(session).generate_candidates(**params)


def _ids(candidates):
    return [c.image_id for c in candidates]


@pytest.mark.asyncio(loop_scope="session")
async def test_the_eligible_set_excludes_inactive_flagged_and_profile_less_images(db_session):
    good = await _image(db_session)
    pending = await _image(db_session, status="pending")
    flagged = await _image(db_session, flagged=True)
    unflagged = await _image(db_session, flagged=False)
    no_profile = await _image(db_session)
    for image in (good, pending, flagged, unflagged):
        await _profile(db_session, image, vector=_vec(1.0, 0.1))

    result = _ids(await _search(db_session))

    assert str(good.id) in result and str(unflagged.id) in result
    for excluded in (pending, flagged, no_profile):
        assert str(excluded.id) not in result


@pytest.mark.asyncio(loop_scope="session")
async def test_the_restriction_limits_every_source(db_session):
    inside = await _image(db_session)
    outside = await _image(db_session)
    for image in (inside, outside):
        await _profile(db_session, image, vector=_vec(1.0), emotion=[("anger", 0.9)])

    result = _ids(await _search(db_session, query_axes={"emotion": (("anger", 0.9),)},
                                restrict_ids={inside.id}))

    assert result == [str(inside.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_source_orders_by_cosine_uses_the_configured_model_and_fills_cosine(db_session):
    near = await _image(db_session)
    far = await _image(db_session)
    other_model = await _image(db_session)
    await _profile(db_session, near, vector=_vec(1.0, 0.1))
    await _profile(db_session, far, vector=_vec(0.0, 1.0))
    await _profile(db_session, other_model, vector=_vec(1.0, 0.0), model="another-model")

    candidates = await _search(db_session, restrict_ids={near.id, far.id, other_model.id})

    assert _ids(candidates) == [str(near.id), str(far.id)]
    assert candidates[0].cosine == pytest.approx(1.0 / (1.01 ** 0.5), abs=1e-4)
    assert candidates[1].cosine == pytest.approx(0.0, abs=1e-4)
    assert all(c.axis_path for c in candidates)


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_source_ranks_exact_before_neighbour_matches_and_skips_unrelated(db_session):
    exact = await _image(db_session)
    neighbour = await _image(db_session)
    unrelated = await _image(db_session)
    await _profile(db_session, exact, emotion=[("anger", 0.9)])
    await _profile(db_session, neighbour, emotion=[("annoyance", 0.9)])
    await _profile(db_session, unrelated, emotion=[("joy", 0.9)])

    candidates = await _search(
        db_session, query_vector=None, query_axes={"emotion": (("anger", 0.9),)},
        neighbours={"emotion": {"anger": (("annoyance", 0.8),)}},
        restrict_ids={exact.id, neighbour.id, unrelated.id})

    assert _ids(candidates) == [str(exact.id), str(neighbour.id)]
    assert candidates[0].values["emotion"] == (("anger", 0.9),) and candidates[0].cosine is None


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_filters_are_or_within_an_axis_and_and_across_axes(db_session):
    a = await _image(db_session)
    b = await _image(db_session)
    c = await _image(db_session)
    await _profile(db_session, a, vector=_vec(1.0), emotion=[("anger", 0.9)], stance=[("mock", 0.9)])
    await _profile(db_session, b, vector=_vec(1.0), emotion=[("joy", 0.9)], stance=[("mock", 0.9)])
    await _profile(db_session, c, vector=_vec(1.0), emotion=[("anger", 0.9)])
    scope = {a.id, b.id, c.id}

    or_within = await _search(db_session, restrict_ids=scope,
                              filters=Filters(axes={"emotion": ("anger", "joy")}))
    and_across = await _search(db_session, restrict_ids=scope,
                               filters=Filters(axes={"emotion": ("anger",), "stance": ("mock",)}))

    assert set(_ids(or_within)) == {str(a.id), str(b.id), str(c.id)}
    assert _ids(and_across) == [str(a.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_tag_filters_keep_the_existing_any_pair_semantics(db_session):
    tagged = await _image(db_session)
    other = await _image(db_session)
    for image in (tagged, other):
        await _profile(db_session, image, vector=_vec(1.0))
    db_session.add(ImageTag(image_id=tagged.id, key="category", value="work", source="rule"))
    await db_session.flush()

    result = _ids(await _search(db_session, restrict_ids={tagged.id, other.id},
                                filters=Filters(tags={"category": ("work",)})))

    assert result == [str(tagged.id)]


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_source_adds_unprofiled_images_flagged_as_not_axis_path(db_session):
    profiled = await _image(db_session)
    lexical_only = await _image(db_session)
    await _profile(db_session, profiled, vector=_vec(1.0))
    db_session.add(OCRLemma(image_id=lexical_only.id, lemma="deadline"))
    await db_session.flush()
    scope = {profiled.id, lexical_only.id}

    candidates = await _search(db_session, lexical_text="deadline", restrict_ids=scope)
    by_id = {c.image_id: c for c in candidates}

    assert by_id[str(lexical_only.id)].axis_path is False and by_id[str(lexical_only.id)].lexical == pytest.approx(1.0)
    assert by_id[str(lexical_only.id)].values == {} and by_id[str(lexical_only.id)].cosine is None
    assert by_id[str(profiled.id)].axis_path is True and by_id[str(profiled.id)].lexical == 0.0


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_filters_exclude_unprofiled_lexical_hits(db_session):
    lexical_only = await _image(db_session)
    db_session.add(OCRLemma(image_id=lexical_only.id, lemma="deadline"))
    await db_session.flush()

    candidates = await _search(db_session, lexical_text="deadline", restrict_ids={lexical_only.id},
                               filters=Filters(axes={"emotion": ("anger",)}))

    assert candidates == []


@pytest.mark.asyncio(loop_scope="session")
async def test_inactive_and_flagged_images_never_come_from_the_lexical_source(db_session):
    inactive = await _image(db_session, status="pending")
    flagged = await _image(db_session, flagged=True)
    for image in (inactive, flagged):
        db_session.add(OCRLemma(image_id=image.id, lemma="deadline"))
    await db_session.flush()

    assert await _search(db_session, lexical_text="deadline", restrict_ids={inactive.id, flagged.id}) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_clusters_are_attached(db_session):
    first = await _image(db_session)
    second = await _image(db_session)
    single = await _image(db_session)
    for image in (first, second, single):
        await _profile(db_session, image, vector=_vec(1.0))
    db_session.add_all([TmpImageClusters(cluster_id=870001, image_id=first.id),
                        TmpImageClusters(cluster_id=870001, image_id=second.id)])
    await db_session.flush()

    by_id = {c.image_id: c for c in await _search(db_session, restrict_ids={first.id, second.id, single.id})}

    assert by_id[str(first.id)].cluster_id == by_id[str(second.id)].cluster_id == 870001
    assert by_id[str(single.id)].cluster_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_the_total_cap_applies_and_sources_are_merged_round_robin(db_session):
    images = [await _image(db_session) for _ in range(4)]
    for index, image in enumerate(images):
        await _profile(db_session, image, vector=_vec(1.0, index * 0.1), emotion=[("anger", 0.9)])
    scope = {image.id for image in images}

    candidates = await _search(db_session, restrict_ids=scope, query_axes={"emotion": (("anger", 0.9),)}, total_cap=3)

    assert len(candidates) == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_to_search_gives_nothing(db_session):
    assert await _search(db_session, query_vector=None) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_load_basics_returns_filename_and_flagged(db_session):
    plain = await _image(db_session, filename="plain.jpg")
    flagged = await _image(db_session, filename="flagged.jpg", flagged=True)

    basics = await MemeRetrievalRepository(db_session).load_basics({str(plain.id), str(flagged.id)})

    assert basics == {str(plain.id): ("plain.jpg", False), str(flagged.id): ("flagged.jpg", True)}


def test_the_repository_never_commits():
    assert "commit(" not in inspect.getsource(meme_retrieval)


# ----------------------------------------------------------------- Task 8b: result-preserving latency changes

def test_vector_literal_round_trips_float32_and_stays_compact():
    import numpy as np
    rng = np.random.default_rng(3)
    vector = rng.standard_normal(PROFILE_EMBEDDING_DIM).astype(np.float32)
    vector = (vector / np.linalg.norm(vector)).tolist()

    literal = meme_retrieval._vector_literal(vector)

    parsed = np.array(literal.strip("[]").split(","), dtype=np.float32)
    assert parsed.shape == (PROFILE_EMBEDDING_DIM,)
    assert np.allclose(parsed, np.array(vector, dtype=np.float32), atol=1e-6, rtol=0)
    assert literal.startswith("[") and literal.endswith("]")
    assert len(literal.encode()) < 15_000   # leaves the situation statement under the 16,384-byte request size


@pytest.mark.asyncio(loop_scope="session")
async def test_more_than_600_ids_load_through_the_array_parameter(db_session):
    images = [await _image(db_session) for _ in range(700)]
    for image in images:
        await _profile(db_session, image, vector=_vec(1.0, 0.1), emotion=[("joy", 0.9)])
    repo = MemeRetrievalRepository(db_session)

    basics = await repo.load_basics([str(image.id) for image in images])
    candidates = await repo._load([image.id for image in images], _vec(1.0), MODEL, {})
    restricted = await _search(db_session, restrict_ids={image.id for image in images}, situation_k=700,
                               axis_k=10, total_cap=700)

    assert len(basics) == 700
    assert [c.image_id for c in candidates] == [str(image.id) for image in images]
    assert all(c.cosine is not None and c.axis_path for c in candidates)
    assert len(restricted) == 700


@pytest.mark.asyncio(loop_scope="session")
async def test_cosines_are_filled_for_axis_only_candidates_and_none_without_an_embedding(db_session):
    near = await _image(db_session)
    await _profile(db_session, near, vector=_vec(1.0, 0.0), emotion=[("joy", 0.9)])
    axis_only = await _image(db_session)       # embedding exists, but is not among the single situation hit
    await _profile(db_session, axis_only, vector=_vec(0.6, 0.8), emotion=[("joy", 0.9)])
    other_model = await _image(db_session)
    await _profile(db_session, other_model, vector=_vec(1.0, 0.0), model="another-model", emotion=[("joy", 0.9)])
    no_embedding = await _image(db_session)
    await _profile(db_session, no_embedding, emotion=[("joy", 0.9)])

    result = {c.image_id: c for c in await _search(
        db_session, query_vector=_vec(1.0), query_axes={"emotion": (("joy", 0.9),)}, situation_k=1)}

    assert result[str(near.id)].cosine == pytest.approx(1.0, abs=1e-6)
    assert result[str(axis_only.id)].cosine == pytest.approx(0.6, abs=1e-6)
    assert result[str(other_model.id)].cosine is None
    assert result[str(no_embedding.id)].cosine is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unsupported_iterative_scan_setting_keeps_the_session_usable_and_ef_search_applied(
        db_session, monkeypatch):
    from sqlalchemy import text
    monkeypatch.setattr(meme_retrieval, "_iterative_scan_supported", None)
    monkeypatch.setattr(meme_retrieval, "ITERATIVE_SCAN_SETTING", "hnsw.no_such_setting")
    repo = MemeRetrievalRepository(db_session)
    await repo._situation_ids(_vec(1.0), MODEL, 5, MemeRetrievalRepository._eligible_ids(repo, Filters(), None))
    monkeypatch.setattr(meme_retrieval, "_iterative_scan_supported", None)

    await repo._prepare_vector_search(123)

    assert meme_retrieval._iterative_scan_supported is False
    assert (await db_session.execute(text("SHOW hnsw.ef_search"))).scalar_one() == "123"
    await repo._prepare_vector_search(77)      # later calls skip the failing setting without a probe
    assert (await db_session.execute(text("SHOW hnsw.ef_search"))).scalar_one() == "77"


@pytest.mark.asyncio(loop_scope="session")
async def test_another_error_does_not_cache_iterative_scan_as_unsupported(db_session, monkeypatch):
    from sqlalchemy import text
    monkeypatch.setattr(meme_retrieval, "_iterative_scan_supported", None)
    repo = MemeRetrievalRepository(db_session)
    real_execute = db_session.execute
    calls = {"both": 0}

    async def flaky(statement, *args, **kwargs):
        if "hnsw.iterative_scan" in str(statement):
            calls["both"] += 1
            if calls["both"] == 1:
                await real_execute(text("SET LOCAL statement_timeout = 1"))
                return await real_execute(text("SELECT pg_sleep(0.5)"))   # 57014 query_canceled
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", flaky)

    await repo._prepare_vector_search(150)       # first probe fails with a timeout: not cached, ef_search still set

    assert meme_retrieval._iterative_scan_supported is None
    assert (await real_execute(text("SHOW hnsw.ef_search"))).scalar_one() == "150"
    await repo._prepare_vector_search(160)       # probes again and succeeds
    assert meme_retrieval._iterative_scan_supported is True
    assert calls["both"] == 2
    assert (await real_execute(text("SHOW hnsw.ef_search"))).scalar_one() == "160"
    monkeypatch.setattr(meme_retrieval, "_iterative_scan_supported", None)


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_restrict_ids_returns_nothing_while_none_is_unrestricted(db_session):
    profiled = await _image(db_session)
    await _profile(db_session, profiled, vector=_vec(1.0), emotion=[("joy", 0.9)])
    db_session.add(OCRLemma(image_id=profiled.id, lemma="deadline"))
    await db_session.flush()
    axes = {"emotion": (("joy", 0.9),)}

    assert await _search(db_session, query_axes=axes, restrict_ids=set()) == []
    assert await _search(db_session, lexical_text="deadline", query_vector=None, restrict_ids=set()) == []
    assert str(profiled.id) in _ids(await _search(db_session, query_axes=axes, restrict_ids=None))
    assert str(profiled.id) in _ids(await _search(db_session, lexical_text="deadline", restrict_ids=None))


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_with_an_axis_filter_keeps_only_profiled_hits(db_session):
    profiled = await _image(db_session)
    await _profile(db_session, profiled, emotion=[("joy", 0.9)])
    unprofiled = await _image(db_session)
    for image in (profiled, unprofiled):
        db_session.add(OCRLemma(image_id=image.id, lemma="deadline"))
    await db_session.flush()

    result = await _search(db_session, query_vector=None, lexical_text="deadline",
                           filters=Filters(axes={"emotion": ("joy",)}), restrict_ids={profiled.id, unprofiled.id})

    assert _ids(result) == [str(profiled.id)]


def test_vector_literal_rejects_non_finite_values():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="non-finite"):
            meme_retrieval._vector_literal([0.5, bad])
