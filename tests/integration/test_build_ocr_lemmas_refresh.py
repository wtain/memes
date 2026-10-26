"""build_ocr_lemmas --refresh-stale (screenshot recut R3c). Requires a live PostgreSQL instance -- see conftest."""
import uuid

import pytest
from sqlalchemy import select

from batch.build_ocr_lemmas import refresh_stale_lemmas
from rules.normalize import make_morph
from Storage.models import Image, ImageStatus, OCRLemma, OCRText
from Storage.stale import images_with_stale_rows

_MORPH = make_morph()


async def _image(session, status=ImageStatus.CUT_PROPOSED):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


async def _refresh(session):
    return await refresh_stale_lemmas(session, _MORPH, 0.0, 0.0, 3)


async def _lemmas(session, image):
    return (await session.execute(
        select(OCRLemma.lemma, OCRLemma.stale).where(OCRLemma.image_id == image.id))).all()


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_replaces_stale_lemmas_with_the_new_ocr(db_session):
    cut = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=cut.id, text="cats sleeping", confidence=0.9, language="en", lang_score=0.9, stale=False),
        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True),
    ])
    await db_session.flush()

    stats = await _refresh(db_session)

    rows = await _lemmas(db_session, cut)
    assert rows and all(stale is False for _, stale in rows)
    assert "stalecopy" not in [lemma for lemma, _ in rows]
    assert stats["refreshed"] >= 1


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_skips_an_image_whose_ocr_is_still_stale(db_session):
    cut = await _image(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="cats", language="en", stale=True),
                        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True)])
    await db_session.flush()

    stats = await _refresh(db_session)

    assert stats["skipped_upstream_stale"] >= 1
    assert await _lemmas(db_session, cut) == [("stalecopy", True)]


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_with_no_ocr_text_leaves_no_stale_lemmas(db_session):
    cut = await _image(db_session)
    db_session.add(OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True))
    await db_session.flush()

    await _refresh(db_session)

    assert await _lemmas(db_session, cut) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_ignores_active_images_and_a_second_run_selects_nothing_of_the_cut(db_session):
    active = await _image(db_session, ImageStatus.ACTIVE)
    db_session.add(OCRLemma(image_id=active.id, lemma="keep", stale=True))
    cut = await _image(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="cats", language="en", confidence=0.9, lang_score=0.9),
                        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True)])
    await db_session.flush()

    await _refresh(db_session)
    await _refresh(db_session)

    assert await _lemmas(db_session, active) == [("keep", True)]
    assert cut.id not in await images_with_stale_rows(db_session, OCRLemma, ImageStatus.CUT_PROPOSED)


@pytest.mark.asyncio(loop_scope="session")
async def test_a_failure_in_the_middle_leaves_the_stale_lemmas(db_session, monkeypatch):
    cut = await _image(db_session)
    db_session.add_all([OCRText(image_id=cut.id, text="cats", language="en", confidence=0.9, lang_score=0.9),
                        OCRLemma(image_id=cut.id, lemma="stalecopy", stale=True)])
    cut_id = cut.id
    await db_session.commit()  # the job's rollback must land on the seeded state, not on an empty one

    from batch import build_ocr_lemmas

    async def boom(self, image_id, lemmas):
        raise RuntimeError("boom")

    monkeypatch.setattr(build_ocr_lemmas.OCRLemmasSaver, "add_lemmas", boom)
    with pytest.raises(RuntimeError):
        await _refresh(db_session)

    rows = (await db_session.execute(
        select(OCRLemma.lemma, OCRLemma.stale).where(OCRLemma.image_id == cut_id))).all()
    assert rows == [("stalecopy", True)]
