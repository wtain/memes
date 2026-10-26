import uuid

import pytest
from sqlalchemy import select

from repository.cut_copier import CutCopier
from Storage.models import (DescriptionLemma, DescriptionNote, DescriptionNoteEmbedding, DescriptionNoteLemma,
                            Embedding, Image, ImageClassification, ImageDescription, ImageDescriptionEmbedding,
                            ImageDescriptionFeedback, ImageExtras, ImageTag, MemeProfile, MemeProfileValue,
                            OCRLemma, OCRText)

CROP = (10, 10, 100, 50)


async def _image(s, **kw):
    img = Image(filename=f"{uuid.uuid4()}.png", status=kw.pop("status", "active"), width=200, height=100, **kw)
    s.add(img)
    await s.flush()
    return img


async def _rows(s, model, image_id):
    return (await s.execute(select(model).where(model.image_id == image_id))).scalars().all()


async def _source_with_everything(s):
    src, cut = await _image(s), await _image(s, status="cut_proposed")
    s.add(DescriptionNote(image_id=src.id, text="my note"))
    await s.flush()
    s.add_all([
        DescriptionNoteEmbedding(description_note_id=src.id, embedding=[0.1] * 1024),
        DescriptionNoteLemma(image_id=src.id, lemma="note"),
        ImageExtras(image_id=src.id, flagged=True, flagged_reason="x", remarks="keep me"),
        ImageTag(image_id=src.id, key="k", value="n", source="Note"),
        ImageTag(image_id=src.id, key="k", value="o", source="OCR"),
        ImageTag(image_id=src.id, key="k", value="l", source="Ollama"),
        ImageTag(image_id=src.id, key="k", value="y", source="YOLO"),
        ImageTag(image_id=src.id, key="k", value="c", source="CONCEPT"),
        OCRText(image_id=src.id, text="inside", confidence=0.9, bbox=[[20, 20], [40, 20], [40, 30], [20, 30]]),
        OCRText(image_id=src.id, text="outside", confidence=0.9, bbox=[[180, 80], [190, 80], [190, 90], [180, 90]]),
        OCRText(image_id=src.id, text="nobox", confidence=0.9, bbox=None),
        OCRLemma(image_id=src.id, lemma="inside"),
        Embedding(image_id=src.id, embedding=[0.1] * 512),
        ImageClassification(image_id=src.id, classifier="text_heavy", result="x"),
        MemeProfile(image_id=src.id, taxonomy_version=1, extractor_model="m", extractor_prompt_version="p",
                    inputs_fingerprint="f" * 64, situation_text="sit", human_verified=True),
    ])
    desc = ImageDescription(image_id=src.id, prompt_key="p", model_used="m", text="a cat")
    s.add(desc)
    await s.flush()
    s.add_all([
        ImageDescriptionEmbedding(image_description_id=desc.id, embedding=[0.2] * 1024),
        ImageDescriptionFeedback(image_description_id=desc.id, approved=False),
        DescriptionLemma(image_description_id=desc.id, lemma="cat"),
        MemeProfileValue(image_id=src.id, axis="emotion", value_key="joy", confidence=0.9, source="model"),
    ])
    await s.flush()
    return src, cut, desc


@pytest.mark.asyncio(loop_scope="session")
async def test_group_1_is_copied_unstale(db_session):
    src, cut, _ = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    note = await db_session.get(DescriptionNote, cut.id)
    assert note.text == "my note"
    assert await db_session.get(DescriptionNoteEmbedding, cut.id) is not None
    assert [r.lemma for r in await _rows(db_session, DescriptionNoteLemma, cut.id)] == ["note"]
    (extras,) = await _rows(db_session, ImageExtras, cut.id)
    assert extras.remarks == "keep me" and extras.flagged is None and extras.flagged_reason is None
    tags = await _rows(db_session, ImageTag, cut.id)
    note_tags = [t for t in tags if t.source == "Note"]
    assert len(note_tags) == 1 and note_tags[0].stale is False
    profile = await db_session.get(MemeProfile, cut.id)
    assert profile.human_verified is True and profile.situation_text == "sit"
    assert len(await _rows(db_session, MemeProfileValue, cut.id)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_group_3_is_copied_stale_and_ocr_is_shifted(db_session):
    src, cut, _ = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    ocr = {r.text: r for r in await _rows(db_session, OCRText, cut.id)}
    assert set(ocr) == {"inside", "nobox"}  # "outside" has its centre outside the crop
    assert ocr["inside"].bbox == [[10, 10], [30, 10], [30, 20], [10, 20]] and ocr["inside"].stale is True
    assert ocr["nobox"].stale is True
    assert all(r.stale for r in await _rows(db_session, OCRLemma, cut.id))
    tags = await _rows(db_session, ImageTag, cut.id)
    assert {t.source for t in tags} == {"Note", "OCR", "Ollama"}
    assert all(t.stale for t in tags if t.source in ("OCR", "Ollama"))


@pytest.mark.asyncio(loop_scope="session")
async def test_descriptions_are_copied_with_their_children_under_new_ids(db_session):
    src, cut, desc = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    (new,) = await _rows(db_session, ImageDescription, cut.id)
    assert new.id != desc.id and new.text == "a cat" and new.stale is True
    assert await db_session.get(ImageDescriptionEmbedding, new.id) is not None
    assert (await db_session.get(ImageDescriptionFeedback, new.id)).approved is False
    lemmas = (await db_session.execute(select(DescriptionLemma).where(DescriptionLemma.image_description_id == new.id))
              ).scalars().all()
    assert [l.lemma for l in lemmas] == ["cat"]
    assert len(await _rows(db_session, ImageDescription, src.id)) == 1  # the source keeps its own


@pytest.mark.asyncio(loop_scope="session")
async def test_group_2_and_the_source_are_untouched(db_session):
    src, cut, _ = await _source_with_everything(db_session)
    await CutCopier(db_session).copy(src.id, cut.id, CROP)
    assert await _rows(db_session, Embedding, cut.id) == []
    assert await _rows(db_session, ImageClassification, cut.id) == []
    assert {t.source for t in await _rows(db_session, ImageTag, cut.id)}.isdisjoint({"YOLO", "CONCEPT"})
    assert len(await _rows(db_session, OCRText, src.id)) == 3
    (src_extras,) = await _rows(db_session, ImageExtras, src.id)
    assert src_extras.flagged is True


@pytest.mark.asyncio(loop_scope="session")
async def test_a_source_with_nothing_to_copy_copies_nothing(db_session):
    src, cut = await _image(db_session), await _image(db_session, status="cut_proposed")
    counts = await CutCopier(db_session).copy(src.id, cut.id, CROP)
    assert sum(counts.values()) == 0
