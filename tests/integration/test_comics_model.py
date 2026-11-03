import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from Storage.models import Comics, ComicsPanel, ComicsState, Image, PopularityAbsorption


async def make_image(session, filename="a.jpg"):
    img = Image(filename=filename, status="active")
    session.add(img)
    await session.flush()
    return img


async def make_comics(session, name="One", **kw):
    c = Comics(name=name, name_key=name.casefold(), state=kw.pop("state", ComicsState.DRAFT), **kw)
    session.add(c)
    await session.flush()
    return c


def panel(comics, image, position, name="p.jpg"):
    return ComicsPanel(comics_id=comics.id, image_id=image.id if image else None, position=position,
                       source_filename=name, source_content_hash="h")


@pytest.mark.asyncio(loop_scope="session")
async def test_name_key_is_unique(db_session):
    await make_comics(db_session, "A")
    db_session.add(Comics(name="a2", name_key="a", state="draft"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_one_comics_per_image(db_session):
    c1, c2 = await make_comics(db_session, "A"), await make_comics(db_session, "B")
    img = await make_image(db_session)
    db_session.add(panel(c1, img, 0))
    await db_session.flush()
    db_session.add(panel(c2, img, 0))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_null_image_panels_do_not_collide(db_session):
    c = await make_comics(db_session)
    db_session.add_all([panel(c, None, 0), panel(c, None, 1)])
    await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_position_unique_but_deferrable(db_session):
    c = await make_comics(db_session)
    p0, p1 = panel(c, None, 0), panel(c, None, 1)
    db_session.add_all([p0, p1])
    await db_session.flush()
    # swap inside one transaction: allowed while the constraint is deferred
    p0.position, p1.position = 1, 0
    await db_session.flush()
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    # a duplicate is refused once checked
    p1.position = 1
    with pytest.raises(IntegrityError):
        await db_session.flush()
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


@pytest.mark.asyncio(loop_scope="session")
async def test_position_must_not_be_negative(db_session):
    c = await make_comics(db_session)
    db_session.add(panel(c, None, -1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_state_check_rejects_other_values(db_session):
    db_session.add(Comics(name="X", name_key="x", state="archived"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_an_image_nulls_the_panel_and_keeps_the_snapshot(db_session):
    c = await make_comics(db_session)
    img = await make_image(db_session)
    p = panel(c, img, 0, name="keep.jpg")
    db_session.add(p)
    await db_session.flush()
    await db_session.execute(delete(Image).where(Image.id == img.id))
    await db_session.refresh(p)
    assert p.image_id is None
    assert p.source_filename == "keep.jpg"


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_a_comics_cascades_to_its_panels(db_session):
    c = await make_comics(db_session)
    p = panel(c, None, 0)
    db_session.add(p)
    await db_session.flush()
    pid = p.id
    await db_session.execute(delete(Comics).where(Comics.id == c.id))
    db_session.expunge_all()
    assert await db_session.get(ComicsPanel, pid) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_the_built_image_nulls_built_image_id(db_session):
    img = await make_image(db_session, "built.jpg")
    c = await make_comics(db_session, state=ComicsState.BUILT, built_image_id=img.id, built_content_hash="h")
    await db_session.execute(delete(Image).where(Image.id == img.id))
    await db_session.refresh(c)
    assert c.built_image_id is None
    assert c.built_content_hash == "h"


@pytest.mark.asyncio(loop_scope="session")
async def test_popularity_source_comics_build_is_accepted(db_session):
    db_session.add(PopularityAbsorption(kind="absorb", source="comics_build", loser_image_id=None,
                                        winner_image_id=uuid.uuid4(), weight=1, dedupe_key="comics-k"))
    await db_session.flush()
