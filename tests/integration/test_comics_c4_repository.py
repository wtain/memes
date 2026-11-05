"""Repository additions of the comics build (epic multi-image-comics, C4) and C1 follow-up tests."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from Backend.app.repositories.image_repository import ImageRepository
from repository.comics import ComicsError, ComicsRepository
from Storage.models import ComicsPanel, Image, ImageExtras

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _image(s, content_hash="h"):
    img = Image(filename=f"{uuid.uuid4()}.png", status="active", width=100, height=100, content_hash=content_hash)
    s.add(img)
    await s.flush()
    return img


async def _filled(s, repo, name, n):
    comics = await repo.create(name)
    panels = [await repo.add_image(comics.id, (await _image(s)).id) for _ in range(n)]
    return comics, panels


async def test_release_panels_sets_kept_and_clears_image_id_only_for_given_ids(db_session):
    repo = ComicsRepository(db_session)
    comics, panels = await _filled(db_session, repo, "Release", 3)
    ids = [panels[0].id, panels[2].id]
    await repo.release_panels(ids)
    await repo.release_panels([])
    rows = (await db_session.execute(
        select(ComicsPanel.id, ComicsPanel.kept, ComicsPanel.image_id).where(ComicsPanel.comics_id == comics.id)
        .execution_options(populate_existing=True))).all()
    by_id = {r.id: r for r in rows}
    for pid in ids:
        assert by_id[pid].kept is True and by_id[pid].image_id is None
    assert by_id[panels[1].id].kept is False and by_id[panels[1].id].image_id is not None


async def test_released_image_can_join_another_comics(db_session):
    repo = ComicsRepository(db_session)
    first = await repo.create("First")
    img = await _image(db_session)
    panel = await repo.add_image(first.id, img.id)
    await repo.release_panels([panel.id])
    second = await repo.create("Second")
    added = await repo.add_image(second.id, img.id)
    assert added.image_id == img.id


async def test_get_flag_reason(db_session):
    repo = ImageRepository(db_session)
    plain, flagged, no_reason, unflagged = [await _image(db_session) for _ in range(4)]
    db_session.add_all([ImageExtras(image_id=flagged.id, flagged=True, flagged_reason="duplicate"),
                        ImageExtras(image_id=no_reason.id, flagged=True),
                        ImageExtras(image_id=unflagged.id, flagged=False, flagged_reason="stale")])
    await db_session.flush()
    assert await repo.get_flag_reason(plain.id) is None
    assert await repo.get_flag_reason(flagged.id) == "duplicate"
    assert await repo.get_flag_reason(no_reason.id) is None
    assert await repo.get_flag_reason(unflagged.id) is None


async def test_mark_built_translates_unique_violation(db_session, monkeypatch):
    repo = ComicsRepository(db_session)
    other, _ = await _filled(db_session, repo, "Other", 2)
    comics, _ = await _filled(db_session, repo, "Mine", 2)
    built = await _image(db_session)
    await repo.mark_built(other.id, built.id, "h1")

    async def miss(self, image_id):  # the pre-check loses a race with another builder
        return False
    monkeypatch.setattr(ComicsRepository, "_built_image_used", miss, raising=False)
    with pytest.raises(ComicsError) as exc:
        await repo.mark_built(comics.id, built.id, "h2")
    assert exc.value.code == "not_buildable" and exc.value.detail == {"reason": "image_already_built"}


async def test_add_image_reraise_branches(db_session, monkeypatch):
    repo = ComicsRepository(db_session)
    comics = await repo.create("Reraise")
    # Branch 1: an integrity error that is not the unique image_id one propagates unchanged (foreign key here).
    ghost = Image(id=uuid.uuid4(), filename="ghost.png", content_hash="g")

    async def fake_addable(self, image_id):
        return ghost
    with monkeypatch.context() as m:
        m.setattr(ComicsRepository, "_check_addable", fake_addable)
        with pytest.raises(IntegrityError) as exc:
            await repo.add_image(comics.id, ghost.id)
    assert "uq_comics_panels_image_id" not in str(exc.value.orig)

    # Branch 2: the unique image_id violation but the owner has vanished by the re-check: the raw error propagates.
    other = await repo.create("Reraise other")
    img = await _image(db_session)
    await repo.add_image(other.id, img.id)

    async def no_owner(self, image_id):
        return None
    monkeypatch.setattr(ComicsRepository, "_panel_owner", no_owner)
    with pytest.raises(IntegrityError) as exc:
        await repo.add_image(comics.id, img.id)
    assert "uq_comics_panels_image_id" in str(exc.value.orig)
