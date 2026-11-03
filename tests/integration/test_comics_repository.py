import uuid

import pytest
from sqlalchemy import func, select

from repository.comics import ComicsError, ComicsRepository
from Storage.models import Comics, ComicsPanel, ComicsState, Image


async def _image(s, status="active"):
    img = Image(filename=f"{uuid.uuid4()}.png", status=status, width=100, height=100)
    s.add(img)
    await s.flush()
    return img


async def _panel(s, comics, position, image):
    s.add(ComicsPanel(comics_id=comics.id, image_id=image.id if image else None, position=position,
                      source_image_id=image.id if image else uuid.uuid4(), source_filename=image.filename if image else "gone.png"))
    await s.flush()


async def _two_panel_comics(s, repo, name="Two"):
    comics = await repo.create(name)
    for i in range(2):
        await _panel(s, comics, i, await _image(s))
    return comics


@pytest.mark.asyncio(loop_scope="session")
async def test_create_normalizes_and_stores_the_key(db_session):
    comics = await ComicsRepository(db_session).create("  Café  ")
    assert comics.name == "Café"
    assert comics.name_key == "café"
    assert comics.state == ComicsState.DRAFT


@pytest.mark.asyncio(loop_scope="session")
async def test_create_with_an_invalid_name_raises_name_invalid(db_session):
    with pytest.raises(ComicsError) as exc:
        await ComicsRepository(db_session).create("a/b")
    assert exc.value.code == "name_invalid"
    assert exc.value.detail["reason"] == "forbidden_character"


@pytest.mark.asyncio(loop_scope="session")
async def test_names_differing_by_case_or_accents_are_taken(db_session):
    repo = ComicsRepository(db_session)
    await repo.create("Café")
    with pytest.raises(ComicsError) as exc:
        await repo.create("CAFÉ")  # decomposed accent, upper case
    assert exc.value.code == "name_taken"


@pytest.mark.asyncio(loop_scope="session")
async def test_rename_a_draft(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Old")
    renamed = await repo.rename(comics.id, " New ")
    assert (renamed.name, renamed.name_key) == ("New", "new")


@pytest.mark.asyncio(loop_scope="session")
async def test_rename_to_a_taken_name_raises_name_taken(db_session):
    repo = ComicsRepository(db_session)
    await repo.create("Taken")
    other = await repo.create("Other")
    with pytest.raises(ComicsError) as exc:
        await repo.rename(other.id, "TAKEN")
    assert exc.value.code == "name_taken"


@pytest.mark.asyncio(loop_scope="session")
async def test_renaming_to_its_own_name_with_other_case_is_allowed(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("story")
    renamed = await repo.rename(comics.id, "STORY")
    assert renamed.name == "STORY"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_unknown_raises_not_found(db_session):
    with pytest.raises(ComicsError) as exc:
        await ComicsRepository(db_session).get(uuid.uuid4())
    assert exc.value.code == "not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_loads_panels_in_order(db_session):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Ordered")
    for pos in (2, 0, 1):
        await _panel(db_session, comics, pos, await _image(db_session))
    got = await repo.get(comics.id)
    assert [p.position for p in got.panels] == [0, 1, 2]


@pytest.mark.asyncio(loop_scope="session")
async def test_search_is_case_insensitive_substring_and_paged(db_session):
    repo = ComicsRepository(db_session)
    for n in ("zq-alpha", "ZQ-Beta", "zq-gamma", "unrelated"):
        await repo.create(n)
    found = await repo.search("ZQ-")
    assert {c.name for c in found} == {"zq-alpha", "ZQ-Beta", "zq-gamma"}
    p1 = await repo.search("zq-", limit=2, offset=0)
    p2 = await repo.search("zq-", limit=2, offset=2)
    assert len(p1) == 2 and len(p2) == 1
    assert {c.id for c in p1} | {c.id for c in p2} == {c.id for c in found}
    assert {c.id for c in p1}.isdisjoint({c.id for c in p2})
    everything = await repo.search(None)
    assert {c.name for c in everything} >= {"unrelated", "zq-alpha"}
    assert {c.id for c in await repo.search("")} == {c.id for c in everything}


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_a_draft_removes_its_panels(db_session):
    repo = ComicsRepository(db_session)
    comics = await _two_panel_comics(db_session, repo)
    cid = comics.id
    await repo.delete(cid)
    assert (await db_session.execute(
        select(func.count()).select_from(ComicsPanel).where(ComicsPanel.comics_id == cid))).scalar_one() == 0
    assert (await db_session.execute(
        select(func.count()).select_from(Comics).where(Comics.id == cid))).scalar_one() == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_unknown_raises_not_found(db_session):
    with pytest.raises(ComicsError) as exc:
        await ComicsRepository(db_session).delete(uuid.uuid4())
    assert exc.value.code == "not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_built_sets_state_and_leaves_panels(db_session):
    repo = ComicsRepository(db_session)
    comics = await _two_panel_comics(db_session, repo)
    built = await _image(db_session)
    result = await repo.mark_built(comics.id, built.id, "abc123")
    assert result.state == ComicsState.BUILT
    assert result.built_image_id == built.id
    assert result.built_content_hash == "abc123"
    assert result.built_at is not None
    assert len(result.panels) == 2
    assert all(p.image_id is not None for p in result.panels)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("case,code,reason", [
    ("one_panel", "not_buildable", "too_few_panels"),
    ("null_panel", "not_buildable", "panel_missing"),
    ("pending_built", "not_buildable", "image_not_active"),
    ("missing_built", "not_buildable", "image_not_active"),
    ("already_built", "built", None),
])
async def test_mark_built_refuses(db_session, case, code, reason):
    repo = ComicsRepository(db_session)
    built = await _image(db_session, status="pending" if case == "pending_built" else "active")
    built_id = uuid.uuid4() if case == "missing_built" else built.id
    if case == "one_panel":
        comics = await repo.create("One")
        await _panel(db_session, comics, 0, await _image(db_session))
    elif case == "null_panel":
        comics = await repo.create("Null")
        await _panel(db_session, comics, 0, await _image(db_session))
        await _panel(db_session, comics, 1, None)
    else:
        comics = await _two_panel_comics(db_session, repo)
    if case == "already_built":
        await repo.mark_built(comics.id, built.id, "h")
        built_id = (await _image(db_session)).id
    with pytest.raises(ComicsError) as exc:
        await repo.mark_built(comics.id, built_id, "h2")
    assert exc.value.code == code
    if reason:
        assert exc.value.detail["reason"] == reason


@pytest.mark.asyncio(loop_scope="session")
async def test_a_built_comics_cannot_be_renamed_or_deleted(db_session):
    repo = ComicsRepository(db_session)
    comics = await _two_panel_comics(db_session, repo)
    await repo.mark_built(comics.id, (await _image(db_session)).id, "h")
    with pytest.raises(ComicsError) as exc:
        await repo.rename(comics.id, "Renamed")
    assert exc.value.code == "built"
    with pytest.raises(ComicsError) as exc:
        await repo.delete(comics.id)
    assert exc.value.code == "built"


@pytest.mark.asyncio(loop_scope="session")
async def test_a_name_race_is_translated_and_leaves_the_session_usable(db_session, monkeypatch):
    repo = ComicsRepository(db_session)
    await repo.create("Race")
    other = await repo.create("Other")
    other_id = other.id

    async def _no_check(*args, **kwargs):
        return None

    monkeypatch.setattr(repo, "_ensure_name_free", _no_check)
    with pytest.raises(ComicsError) as exc:
        await repo.create("RACE")
    assert exc.value.code == "name_taken"
    with pytest.raises(ComicsError) as exc:
        await repo.rename(other_id, "race")
    assert exc.value.code == "name_taken"
    assert (await repo.create("After")).name == "After"
    assert (await repo.get(other_id)).name == "Other"


@pytest.mark.asyncio(loop_scope="session")
async def test_search_matches_non_ascii_case_insensitively(db_session):
    repo = ComicsRepository(db_session)
    created = await repo.create("Привет Мир")
    found = await repo.search("привет")
    assert created.id in {c.id for c in found}


@pytest.mark.asyncio(loop_scope="session")
async def test_search_is_nfc_aware_both_ways(db_session):
    repo = ComicsRepository(db_session)
    composed = await repo.create("Zq café")
    decomposed = await repo.create("Zq naïve")
    assert composed.id in {c.id for c in await repo.search("CAFÉ")}
    assert decomposed.id in {c.id for c in await repo.search("naïve")}


@pytest.mark.asyncio(loop_scope="session")
async def test_search_wildcards_are_literal_and_empty_lists_all(db_session):
    repo = ComicsRepository(db_session)
    await repo.create("plain one")
    await repo.create("plain two")
    assert await repo.search("%") == []
    assert await repo.search("_") == []
    assert await repo.search("?*") == []
    assert len(await repo.search(None)) >= 2
    assert len(await repo.search("")) >= 2


@pytest.mark.asyncio(loop_scope="session")
async def test_get_with_lock_returns_panels_in_order(db_session):
    repo = ComicsRepository(db_session)
    comics = await _two_panel_comics(db_session, repo, "Locked")
    got = await repo.get(comics.id, lock=True)
    assert [p.position for p in got.panels] == [0, 1]


@pytest.mark.asyncio(loop_scope="session")
async def test_mark_built_refuses_a_built_image_used_by_another_comics(db_session):
    repo = ComicsRepository(db_session)
    first = await _two_panel_comics(db_session, repo, "BuiltOne")
    second = await _two_panel_comics(db_session, repo, "BuiltTwo")
    built = await _image(db_session)
    await repo.mark_built(first.id, built.id, "h")
    with pytest.raises(ComicsError) as exc:
        await repo.mark_built(second.id, built.id, "h")
    assert exc.value.code == "not_buildable"
    assert exc.value.detail["reason"] == "image_already_built"
