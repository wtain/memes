"""
Integration tests for Backend/app/repositories/image_repository.py.

Requires a live PostgreSQL instance with pgvector — see tests/integration/conftest.py.

Backend/app/repositories/ has no other test coverage: router tests mock the
service layer and never execute real queries, so a wrong column/attribute
name here (as happened in recommendations_repository.py and
concept_repository.py) would ship silently. These tests exercise every
public method against a real schema.
"""
import uuid

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Storage.models import (
    Embedding,
    Image,
    ImageDescription,
    ImageDescriptionEmbedding,
    ImageExtras,
    ImageTag,
    OCRLemma,
    OCRText,
    TmpDuplicates,
    TmpImageClusters,
)

_DIM = 512


def _unit_vector(index: int) -> list[float]:
    vec = [0.0] * _DIM
    vec[index] = 1.0
    return vec


_TEXT_DIM = 1024


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * _TEXT_DIM
    vec[index] = 1.0
    return vec


async def _insert_description(session, image, prompt_key: str, text: str = "a description") -> ImageDescription:
    description = ImageDescription(
        image_id=image.id, prompt_key=prompt_key, model_used="llava", text=text,
    )
    session.add(description)
    await session.flush()
    return description


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_search_no_filters_returns_all_with_flagged_status(db_session):
    kept = Image(filename=f"{uuid.uuid4()}.jpg")
    flagged = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([kept, flagged])
    await db_session.flush()
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows, facets = await repo.search(q=None, tags={}, cursor_created_at=None, cursor_id=None, limit=50)

    by_id = {r.id: r for r in rows}
    assert kept.id in by_id
    assert by_id[kept.id].flagged is None
    assert flagged.id in by_id
    assert by_id[flagged.id].flagged is True


@pytest.mark.asyncio(loop_scope="session")
async def test_search_filters_by_text(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([matching, other])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=matching.id, lemma="boyfriend"),
        OCRLemma(image_id=other.id, lemma="unrelated"),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows, _ = await repo.search(q="boyfriend", tags={}, cursor_created_at=None, cursor_id=None, limit=50)

    ids = {r.id for r in rows}
    assert matching.id in ids
    assert other.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_search_matches_lemma_regardless_of_query_word_form(db_session):
    matching = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(matching)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=matching.id, lemma="полиция"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows, _ = await repo.search(q="полицию", tags={}, cursor_created_at=None, cursor_id=None, limit=50)

    assert matching.id in {r.id for r in rows}


@pytest.mark.asyncio(loop_scope="session")
async def test_search_filters_by_tags_union_across_keys(db_session):
    animal_match = Image(filename=f"{uuid.uuid4()}.jpg")
    mood_match = Image(filename=f"{uuid.uuid4()}.jpg")
    no_match = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([animal_match, mood_match, no_match])
    await db_session.flush()
    db_session.add_all([
        ImageTag(image_id=animal_match.id, key="animal", value="cat", source="rules"),
        ImageTag(image_id=mood_match.id, key="mood", value="happy", source="rules"),
        ImageTag(image_id=no_match.id, key="animal", value="dog", source="rules"),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows, _ = await repo.search(
        q=None,
        tags={"animal": {"cat"}, "mood": {"happy"}},
        cursor_created_at=None,
        cursor_id=None,
        limit=50,
    )

    ids = {r.id for r in rows}
    assert animal_match.id in ids
    assert mood_match.id in ids
    assert no_match.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_search_facets_count_tag_values(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(ImageTag(image_id=image.id, key="animal", value="cat", source="rules"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    _, facets = await repo.search(q=None, tags={}, cursor_created_at=None, cursor_id=None, limit=50)

    assert facets["animal"]["cat"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_search_cursor_pagination_excludes_current_and_later(db_session):
    older = Image(filename=f"{uuid.uuid4()}.jpg")
    newer = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([older, newer])
    await db_session.flush()

    repo = ImageRepository(db_session)
    all_rows, _ = await repo.search(q=None, tags={}, cursor_created_at=None, cursor_id=None, limit=50)
    all_rows = sorted(all_rows, key=lambda r: (r.created_at, r.id), reverse=True)
    cursor_row = all_rows[0]

    page, _ = await repo.search(
        q=None, tags={}, cursor_created_at=cursor_row.created_at, cursor_id=cursor_row.id, limit=50
    )
    assert cursor_row.id not in {r.id for r in page}


# --------------------------------------------------------------------------
# get_filename / get_texts / get_tags / get_meme_data
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_filename_found_and_missing(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.get_filename(image.id) == image.filename
    assert await repo.get_filename(uuid.uuid4()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_texts_filters_by_confidence_and_ids(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([image, other])
    await db_session.flush()
    db_session.add_all([
        OCRText(image_id=image.id, text="high confidence", confidence=0.9),
        OCRText(image_id=image.id, text="low confidence", confidence=0.1),
        OCRText(image_id=other.id, text="other image text", confidence=0.9),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_texts({image.id}, min_confidence=0.8)

    texts = {r.text for r in rows}
    assert texts == {"high confidence"}


@pytest.mark.asyncio(loop_scope="session")
async def test_get_tags_filters_by_ids(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    other = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([image, other])
    await db_session.flush()
    db_session.add_all([
        ImageTag(image_id=image.id, key="animal", value="cat", source="rules"),
        ImageTag(image_id=other.id, key="animal", value="dog", source="rules"),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_tags({image.id})

    assert [(r.key, r.value) for r in rows] == [("animal", "cat")]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_meme_data_returns_filename_texts_tags(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add_all([
        OCRText(image_id=image.id, text="hello world", confidence=0.9),
        ImageTag(image_id=image.id, key="animal", value="cat", source="rules"),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    filename, texts, tags = await repo.get_meme_data(image.id)

    assert filename == image.filename
    assert texts == ["hello world"]
    assert list(tags) == [("animal", "cat")]


# --------------------------------------------------------------------------
# get_embedding / get_similar
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_embedding_none_when_missing_and_vector_when_present(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.get_embedding(image.id) is None

    vec = _unit_vector(0)
    db_session.add(Embedding(image_id=image.id, embedding=vec))
    await db_session.flush()

    embedding = await repo.get_embedding(image.id)
    assert list(embedding) == pytest.approx(vec)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_excludes_self_and_orders_by_distance(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    near = Image(filename=f"{uuid.uuid4()}.jpg")
    far = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, near, far])
    await db_session.flush()
    db_session.add_all([
        Embedding(image_id=query_image.id, embedding=_unit_vector(0)),
        Embedding(image_id=near.id, embedding=_unit_vector(0)),
        Embedding(image_id=far.id, embedding=_unit_vector(1)),
    ])
    await db_session.flush()
    db_session.add(ImageExtras(image_id=near.id, flagged=True))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar(query_image.id, _unit_vector(0), limit=10)

    ids = [r.image_id for r in rows]
    assert query_image.id not in ids
    assert ids.index(near.id) < ids.index(far.id)
    assert next(r for r in rows if r.image_id == near.id).flagged is True


# --------------------------------------------------------------------------
# get_similar_by_description / has_description_embedding
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_description_matches_only_same_prompt_key_and_excludes_self(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    same_prompt_candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    different_prompt_candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, same_prompt_candidate, different_prompt_candidate])
    await db_session.flush()

    query_desc = await _insert_description(db_session, query_image, "general_description")
    same_desc = await _insert_description(db_session, same_prompt_candidate, "general_description")
    different_desc = await _insert_description(db_session, different_prompt_candidate, "humor_explanation")

    db_session.add_all([
        ImageDescriptionEmbedding(image_description_id=query_desc.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=same_desc.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=different_desc.id, embedding=_text_unit_vector(0)),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar_by_description(query_image.id, limit=10)

    ids = [r.image_id for r in rows]
    assert query_image.id not in ids
    assert same_prompt_candidate.id in ids
    assert different_prompt_candidate.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_description_takes_minimum_distance_across_shared_prompts(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, candidate])
    await db_session.flush()

    query_general = await _insert_description(db_session, query_image, "general_description")
    query_humor = await _insert_description(db_session, query_image, "humor_explanation")
    cand_general = await _insert_description(db_session, candidate, "general_description")
    cand_humor = await _insert_description(db_session, candidate, "humor_explanation")

    db_session.add_all([
        ImageDescriptionEmbedding(image_description_id=query_general.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=query_humor.id, embedding=_text_unit_vector(0)),
        # general_description pair is far (orthogonal); humor_explanation pair is identical (close)
        ImageDescriptionEmbedding(image_description_id=cand_general.id, embedding=_text_unit_vector(1)),
        ImageDescriptionEmbedding(image_description_id=cand_humor.id, embedding=_text_unit_vector(0)),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar_by_description(query_image.id, limit=10)

    row = next(r for r in rows if r.image_id == candidate.id)
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_similar_by_description_includes_flagged_status(db_session):
    query_image = Image(filename=f"{uuid.uuid4()}.jpg")
    flagged_candidate = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([query_image, flagged_candidate])
    await db_session.flush()

    query_desc = await _insert_description(db_session, query_image, "general_description")
    cand_desc = await _insert_description(db_session, flagged_candidate, "general_description")
    db_session.add_all([
        ImageDescriptionEmbedding(image_description_id=query_desc.id, embedding=_text_unit_vector(0)),
        ImageDescriptionEmbedding(image_description_id=cand_desc.id, embedding=_text_unit_vector(0)),
    ])
    db_session.add(ImageExtras(image_id=flagged_candidate.id, flagged=True))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_similar_by_description(query_image.id, limit=10)

    row = next(r for r in rows if r.image_id == flagged_candidate.id)
    assert row.flagged is True


@pytest.mark.asyncio(loop_scope="session")
async def test_has_description_embedding_true_when_present_false_otherwise(db_session):
    embedded_image = Image(filename=f"{uuid.uuid4()}.jpg")
    unembedded_image = Image(filename=f"{uuid.uuid4()}.jpg")
    no_description_image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([embedded_image, unembedded_image, no_description_image])
    await db_session.flush()

    embedded_desc = await _insert_description(db_session, embedded_image, "general_description")
    await _insert_description(db_session, unembedded_image, "general_description")
    db_session.add(ImageDescriptionEmbedding(image_description_id=embedded_desc.id, embedding=_text_unit_vector(0)))
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert await repo.has_description_embedding(embedded_image.id) is True
    assert await repo.has_description_embedding(unembedded_image.id) is False
    assert await repo.has_description_embedding(no_description_image.id) is False


# --------------------------------------------------------------------------
# get_descriptions
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_descriptions_returns_rows_ordered_by_prompt_key(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    await _insert_description(db_session, image, "humor_explanation", text="It's funny because...")
    await _insert_description(db_session, image, "general_description", text="A cat wearing a hat.")
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_descriptions(image.id)

    assert [r.prompt_key for r in rows] == ["general_description", "humor_explanation"]
    assert rows[0].text == "A cat wearing a hat."
    assert rows[0].model_used == "llava"
    assert rows[0].created_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_descriptions_returns_empty_list_when_none_exist(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_descriptions(image.id)

    assert rows == []


# --------------------------------------------------------------------------
# get_untagged / get_no_ocr
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_untagged_excludes_tagged_images(db_session):
    tagged = Image(filename=f"{uuid.uuid4()}.jpg")
    untagged = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([tagged, untagged])
    await db_session.flush()
    db_session.add(ImageTag(image_id=tagged.id, key="animal", value="cat", source="rules"))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_untagged(cursor_created_at=None, cursor_id=None, limit=50)

    ids = {r.id for r in rows}
    assert untagged.id in ids
    assert tagged.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_no_ocr_excludes_images_with_ocr_text(db_session):
    has_ocr = Image(filename=f"{uuid.uuid4()}.jpg")
    no_ocr = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([has_ocr, no_ocr])
    await db_session.flush()
    db_session.add(OCRText(image_id=has_ocr.id, text="hello", confidence=0.9))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_no_ocr(cursor_created_at=None, cursor_id=None, limit=50)

    ids = {r.id for r in rows}
    assert no_ocr.id in ids
    assert has_ocr.id not in ids


# --------------------------------------------------------------------------
# duplicates
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_duplicates_clusters_close_embeddings(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg")
    b = Image(filename=f"{uuid.uuid4()}.jpg")
    unrelated = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b, unrelated])
    await db_session.flush()
    close_vec = _unit_vector(0)
    db_session.add_all([
        Embedding(image_id=a.id, embedding=close_vec),
        Embedding(image_id=b.id, embedding=close_vec),
        Embedding(image_id=unrelated.id, embedding=_unit_vector(1)),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_duplicates(cursor_created_at=None, cursor_id=None, limit=50, threshold=0.01)

    ids = {r[0] for r in rows}
    assert a.id in ids
    assert b.id in ids
    assert unrelated.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_get_duplicates_precomputed_reads_tmp_table(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg")
    b = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add(TmpDuplicates(image_id1=a.id, image_id2=b.id, distance=0.05))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_duplicates_precomputed(cursor_created_at=None, cursor_id=None, limit=50, threshold=0.1)

    matches = [r for r in rows if r[1] == a.id and r[3] == b.id]
    assert len(matches) == 1
    assert matches[0][6] == pytest.approx(0.05)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_duplicates_clustered_reads_tmp_clusters_with_flagged(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg")
    b = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all([
        TmpImageClusters(cluster_id=1, image_id=a.id),
        TmpImageClusters(cluster_id=1, image_id=b.id),
    ])
    db_session.add(ImageExtras(image_id=b.id, flagged=True))
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_duplicates_clustered(cursor_cluster_id=None, cursor_image_id=None, limit=50)

    by_id = {r[0]: r for r in rows}
    assert by_id[a.id][3] == 1
    assert by_id[a.id][4] is None
    assert by_id[b.id][4] is True


# --------------------------------------------------------------------------
# flagged
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_flagged_only_returns_flagged_true(db_session):
    flagged = Image(filename=f"{uuid.uuid4()}.jpg")
    unflagged = Image(filename=f"{uuid.uuid4()}.jpg")
    no_extras = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([flagged, unflagged, no_extras])
    await db_session.flush()
    db_session.add_all([
        ImageExtras(image_id=flagged.id, flagged=True),
        ImageExtras(image_id=unflagged.id, flagged=False),
    ])
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_flagged(cursor_created_at=None, cursor_id=None, limit=50)

    ids = {r.id for r in rows}
    assert flagged.id in ids
    assert unflagged.id not in ids
    assert no_extras.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_set_flagged_inserts_then_updates(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_flagged(image.id, True)
    await db_session.flush()
    assert await repo.get_is_flagged(image.id) is True

    await repo.set_flagged(image.id, False)
    await db_session.flush()
    assert await repo.get_is_flagged(image.id) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_get_is_flagged_falsy_when_no_extras_row(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    assert not await repo.get_is_flagged(image.id)
