import uuid

import pytest
from sqlalchemy import select, text

from repository.comics import ComicsError, ComicsRepository
from Storage.models import ComicsPanel, Image, ImageCut, ImageExtras


async def _image(s, status="active", content_hash="h"):
    img = Image(filename=f"{uuid.uuid4()}.png", status=status, width=100, height=100, content_hash=content_hash)
    s.add(img)
    await s.flush()
    return img


async def _cut(s, source=None, cut=None, state="proposed"):
    s.add(ImageCut(source_image_id=source.id if source else None, cut_image_id=cut.id if cut else None,
                   crop_x=0, crop_y=0, crop_w=10, crop_h=10, detector="d", detector_version="1", state=state))
    await s.flush()


async def _positions(s, comics_id):
    rows = await s.execute(select(ComicsPanel.position).where(ComicsPanel.comics_id == comics_id)
                           .order_by(ComicsPanel.position))
    return [r[0] for r in rows]


async def _filled(s, repo, name, n):
    comics = await repo.create(name)
    panels = []
    for _ in range(n):
        panels.append(await repo.add_image(comics.id, (await _image(s)).id))
    return comics, panels


async def _build(s, comics_id):
    await s.execute(text("UPDATE comics SET state='built' WHERE id=:i"), {"i": comics_id})


@pytest.mark.asyncio(loop_scope="session")
async def test_add_appends_with_contiguous_positions_and_snapshot(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Add")
    a, b = await _image(db_session, content_hash="ha"), await _image(db_session, content_hash="hb")
    pa = await repo.add_image(comics.id, a.id)
    pb = await repo.add_image(comics.id, b.id)
    assert (pa.position, pb.position) == (0, 1)
    assert (pb.source_filename, pb.source_content_hash) == (b.filename, "hb")
    assert await _positions(db_session, comics.id) == [0, 1]


@pytest.mark.parametrize("case", ["pending", "flagged", "live_source", "live_cut", "missing"])
@pytest.mark.asyncio(loop_scope="session")
async def test_add_refuses(db_session, case):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Refuse")
    img = await _image(db_session, status="pending" if case == "pending" else "active")
    image_id = img.id
    if case == "flagged":
        db_session.add(ImageExtras(image_id=img.id, flagged=True))
    elif case == "live_source":
        await _cut(db_session, source=img)
    elif case == "live_cut":
        await _cut(db_session, cut=img)
    elif case == "missing":
        image_id = uuid.uuid4()
    await db_session.flush()
    with pytest.raises(ComicsError) as exc:
        await repo.add_image(comics.id, image_id)
    assert exc.value.code == "image_not_addable"
    assert exc.value.detail["reason"] == {"pending": "not_active", "flagged": "flagged", "live_source": "live_cut",
                                          "live_cut": "live_cut", "missing": "not_found"}[case]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_rejected_cut_does_not_block(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Rej")
    img = await _image(db_session)
    await _cut(db_session, source=img, state="rejected")
    assert (await repo.add_image(comics.id, img.id)).position == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_add_an_image_already_in_a_comics_names_that_comics(db_session):
    repo = ComicsRepository(db_session)
    first, panels = await _filled(db_session, repo, "First", 1)
    other = await repo.create("Other")
    for target in (first, other):
        with pytest.raises(ComicsError) as exc:
            await repo.add_image(target.id, panels[0].image_id)
        assert exc.value.code == "image_in_comics"
        assert exc.value.detail == {"comics_id": first.id, "comics_name": "First"}


@pytest.mark.asyncio(loop_scope="session")
async def test_move_image_between_drafts_renumbers_the_source(db_session):
    repo = ComicsRepository(db_session)
    src, panels = await _filled(db_session, repo, "Src", 3)
    dst, _ = await _filled(db_session, repo, "Dst", 1)
    first = panels[0]
    snap = (first.source_filename, first.source_content_hash)
    moved = await repo.move_image(first.image_id, dst.id)
    assert moved.id == first.id and moved.comics_id == dst.id and moved.position == 1
    assert (moved.source_filename, moved.source_content_hash) == snap
    assert await _positions(db_session, src.id) == [0, 1]
    assert await _positions(db_session, dst.id) == [0, 1]
    with pytest.raises(ComicsError) as exc:
        await repo.move_image(uuid.uuid4(), dst.id)
    assert exc.value.code == "not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_move_refuses_when_either_comics_is_built(db_session):
    repo = ComicsRepository(db_session)
    src, sp = await _filled(db_session, repo, "MSrc", 2)
    dst, dp = await _filled(db_session, repo, "MDst", 2)
    await _build(db_session, dst.id)
    with pytest.raises(ComicsError) as exc:
        await repo.move_image(sp[0].image_id, dst.id)
    assert exc.value.code == "built"
    await _build(db_session, src.id)
    with pytest.raises(ComicsError) as exc:
        await repo.move_image(dp[0].image_id, src.id)
    assert exc.value.code == "built"
    _, free = await _filled(db_session, repo, "MFree", 1)
    with pytest.raises(ComicsError) as exc:
        await repo.move_image(free[0].image_id, uuid.uuid4())
    assert exc.value.code == "not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_panel_renumbers(db_session):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Rm", 3)
    image_id = panels[0].image_id
    panel_id = panels[0].id
    await repo.remove_panel(panel_id)
    assert await _positions(db_session, comics.id) == [0, 1]
    assert (await db_session.get(Image, image_id)) is not None
    with pytest.raises(ComicsError) as exc:
        await repo.remove_panel(panel_id)
    assert exc.value.code == "not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_remove_a_missing_panel_works_after_its_image_is_deleted(db_session):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Gone", 2)
    panel_id = panels[0].id
    await db_session.execute(text("DELETE FROM images WHERE id=:i"), {"i": panels[0].image_id})
    db_session.expire_all()
    await repo.remove_panel(panel_id)
    added = await repo.add_image(comics.id, (await _image(db_session)).id)
    assert added.position == 1
    assert await _positions(db_session, comics.id) == [0, 1]


@pytest.mark.parametrize("case", ["omitted", "repeated", "foreign"])
@pytest.mark.asyncio(loop_scope="session")
async def test_reorder_refuses_a_wrong_set(db_session, case):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Ro", 3)
    _, other = await _filled(db_session, repo, "Ro2", 1)
    ids = [p.id for p in panels]
    bad = {"omitted": ids[:2], "repeated": [ids[0], ids[0], ids[1]],
           "foreign": [ids[0], ids[1], other[0].id]}[case]
    with pytest.raises(ComicsError) as exc:
        await repo.reorder(comics.id, bad)
    assert exc.value.code == "reorder_mismatch"
    rows = await db_session.execute(select(ComicsPanel.id).where(ComicsPanel.comics_id == comics.id)
                                    .order_by(ComicsPanel.position))
    assert [r[0] for r in rows] == ids


@pytest.mark.asyncio(loop_scope="session")
async def test_reorder_applies_the_order(db_session):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Ro3", 3)
    ids = [p.id for p in panels]
    new = [ids[2], ids[0], ids[1]]
    await repo.reorder(comics.id, new)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    rows = await db_session.execute(select(ComicsPanel.id).where(ComicsPanel.comics_id == comics.id)
                                    .order_by(ComicsPanel.position))
    assert [r[0] for r in rows] == new


@pytest.mark.asyncio(loop_scope="session")
async def test_every_panel_operation_refuses_on_a_built_comics(db_session):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Built", 2)
    spare = await _image(db_session)
    image_id, panel_id, ids = panels[0].image_id, panels[0].id, [p.id for p in panels]
    await _build(db_session, comics.id)
    calls = [lambda: repo.add_image(comics.id, spare.id), lambda: repo.move_image(image_id, comics.id),
             lambda: repo.remove_panel(panel_id), lambda: repo.reorder(comics.id, ids)]
    for call in calls:
        with pytest.raises(ComicsError) as exc:
            await call()
        assert exc.value.code == "built"


@pytest.mark.asyncio(loop_scope="session")
async def test_source_image_id_is_a_snapshot_that_survives_the_image_and_a_move(db_session):
    repo = ComicsRepository(db_session)
    src, panels = await _filled(db_session, repo, "SnapSrc", 1)
    dst = await repo.create("SnapDst")
    panel_id, image_id = panels[0].id, panels[0].image_id
    assert panels[0].source_image_id == image_id
    moved = await repo.move_image(image_id, dst.id)
    assert moved.source_image_id == image_id
    await db_session.execute(text("DELETE FROM images WHERE id=:i"), {"i": image_id})
    db_session.expire_all()
    row = (await db_session.execute(select(ComicsPanel.image_id, ComicsPanel.source_image_id)
                                    .where(ComicsPanel.id == panel_id))).one()
    assert row.image_id is None and row.source_image_id == image_id


@pytest.mark.asyncio(loop_scope="session")
async def test_move_into_its_own_comics_places_the_panel_last(db_session):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Own", 3)
    a, b, c = panels
    ids = (a.id, b.id, c.id)
    await repo.move_image(a.image_id, comics.id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    rows = (await db_session.execute(select(ComicsPanel.id, ComicsPanel.position)
                                     .where(ComicsPanel.comics_id == comics.id)
                                     .order_by(ComicsPanel.position))).all()
    assert [r.id for r in rows] == [ids[1], ids[2], ids[0]]
    assert [r.position for r in rows] == [0, 1, 2]


@pytest.mark.asyncio(loop_scope="session")
async def test_image_in_comics_takes_precedence_over_not_addable(db_session):
    repo = ComicsRepository(db_session)
    first, panels = await _filled(db_session, repo, "Prec", 1)
    other = await repo.create("PrecOther")
    db_session.add(ImageExtras(image_id=panels[0].image_id, flagged=True))
    await db_session.flush()
    with pytest.raises(ComicsError) as exc:
        await repo.add_image(other.id, panels[0].image_id)
    assert exc.value.code == "image_in_comics"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_built_target_blocks_add_image(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("BuiltTarget")
    await _build(db_session, comics.id)
    with pytest.raises(ComicsError) as exc:
        await repo.add_image(comics.id, (await _image(db_session)).id)
    assert exc.value.code == "built"


@pytest.mark.asyncio(loop_scope="session")
async def test_positions_are_unique_after_move_and_remove(db_session):
    repo = ComicsRepository(db_session)
    src, panels = await _filled(db_session, repo, "UqSrc", 3)
    dst, _ = await _filled(db_session, repo, "UqDst", 2)
    p0_image, p1_id = panels[0].image_id, panels[1].id
    await repo.move_image(p0_image, dst.id)
    await repo.remove_panel(p1_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await _positions(db_session, src.id) == [0]
    assert await _positions(db_session, dst.id) == [0, 1, 2]


@pytest.mark.asyncio(loop_scope="session")
async def test_add_image_translates_an_image_id_race(db_session, monkeypatch):
    repo = ComicsRepository(db_session)
    first, panels = await _filled(db_session, repo, "RaceA", 1)
    other = await repo.create("RaceB")
    image_id, first_id = panels[0].image_id, first.id

    async def no_owner(self, image_id):
        return None
    real = ComicsRepository._panel_owner
    monkeypatch.setattr(ComicsRepository, "_panel_owner", no_owner)
    calls = []

    async def owner_after(self, image_id):
        calls.append(1)
        return await real(self, image_id)
    # first call (pre-check) sees nothing; the translation re-query uses the real lookup
    state = {"n": 0}

    async def flaky(self, image_id):
        state["n"] += 1
        return None if state["n"] == 1 else await real(self, image_id)
    monkeypatch.setattr(ComicsRepository, "_panel_owner", flaky)
    with pytest.raises(ComicsError) as exc:
        await repo.add_image(other.id, image_id)
    assert exc.value.code == "image_in_comics"
    assert exc.value.detail == {"comics_id": first_id, "comics_name": "RaceA"}
