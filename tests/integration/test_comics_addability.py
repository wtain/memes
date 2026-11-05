import uuid

import pytest
from sqlalchemy import event, text

from repository.comics import ComicsRepository
from Storage.models import Image, ImageCut, ImageExtras


async def _image(s, status="active"):
    img = Image(filename=f"{uuid.uuid4()}.png", status=status, width=100, height=100, content_hash="h")
    s.add(img)
    await s.flush()
    return img


async def _cut(s, source=None, cut=None, state="proposed"):
    s.add(ImageCut(source_image_id=source.id if source else None, cut_image_id=cut.id if cut else None,
                   crop_x=0, crop_y=0, crop_w=10, crop_h=10, detector="d", detector_version="1", state=state))
    await s.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_addability_reasons_and_precedence(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Addability")
    missing = uuid.uuid4()
    pending = await _image(db_session, status="pending")
    flagged = await _image(db_session)
    db_session.add(ImageExtras(image_id=flagged.id, flagged=True))
    live_source, live_cut = await _image(db_session), await _image(db_session)
    await _cut(db_session, source=live_source)
    await _cut(db_session, cut=live_cut)
    held = await _image(db_session)
    await repo.add_image(comics.id, held.id)
    clean = await _image(db_session)
    both = await _image(db_session)
    await repo.add_image(comics.id, both.id)
    db_session.add(ImageExtras(image_id=both.id, flagged=True))
    await db_session.flush()

    ids = [missing, pending.id, flagged.id, live_source.id, live_cut.id, held.id, clean.id, both.id]
    result = await repo.addability(ids)
    assert set(result) == set(ids)
    assert result[missing].reason == "not_found" and not result[missing].addable
    assert result[pending.id].reason == "not_active"
    assert result[flagged.id].reason == "flagged"
    assert result[live_source.id].reason == "live_cut"
    assert result[live_cut.id].reason == "live_cut"
    assert (result[held.id].reason, result[held.id].comics_id, result[held.id].comics_name) == \
        ("in_comics", comics.id, "Addability")
    assert result[clean.id].addable and result[clean.id].reason is None and result[clean.id].comics_id is None
    assert result[both.id].reason == "flagged" and result[both.id].comics_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_addability_runs_a_fixed_number_of_queries(db_session):
    repo = ComicsRepository(db_session)
    ids = [(await _image(db_session)).id for _ in range(30)] + [uuid.uuid4() for _ in range(30)]
    count = []

    def counter(*_args):
        count.append(1)

    sync_engine = db_session.get_bind().engine if hasattr(db_session.get_bind(), "engine") else db_session.get_bind()
    event.listen(sync_engine, "before_cursor_execute", counter)
    try:
        result = await repo.addability(ids)
    finally:
        event.remove(sync_engine, "before_cursor_execute", counter)
    assert len(result) == 60
    assert len(count) <= 5


@pytest.mark.asyncio(loop_scope="session")
async def test_find_by_image_returns_comics_and_panel_or_none(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Finder")
    img, plain, released = await _image(db_session), await _image(db_session), await _image(db_session)
    panel = await repo.add_image(comics.id, img.id)
    released_panel = await repo.add_image(comics.id, released.id)
    found = await repo.find_by_image(img.id)
    assert found is not None and found[0].id == comics.id and found[1].id == panel.id
    assert await repo.find_by_image(plain.id) is None
    await db_session.execute(text("UPDATE comics_panels SET image_id=NULL WHERE id=:i"), {"i": released_panel.id})
    assert await repo.find_by_image(released.id) is None
    built = await repo.create("Built finder")
    kept = await _image(db_session)
    await repo.add_image(built.id, kept.id)
    await db_session.execute(text("UPDATE comics SET state='built' WHERE id=:i"), {"i": built.id})
    await db_session.execute(text("UPDATE comics_panels SET image_id=NULL WHERE comics_id=:i"), {"i": built.id})
    assert await repo.find_by_image(kept.id) is None
