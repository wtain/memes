"""ComicsBuildService: build a draft comics into one active image (epic multi-image-comics, C4).
Requires a live PostgreSQL instance. Panel files are real small images in a temporary BASE_PATH (tmp_path).

Failure tests commit their setup first (db_session commits only a savepoint, see conftest), so the rollback that
get_async_db does after a refused build returns to exactly the state the build started from."""
import asyncio
import os
import re
import shutil
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI
from PIL import Image as PILImage
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.api import comics as comics_api
from Backend.app.api.comics import get_comics_build_service
from Backend.app.api.comics import router as comics_router
from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services import comics_build_service
from Backend.app.services.comics_build_service import ComicsBuildService
from batch.utils.comics_compose import compose_to_file
from batch.utils.file_hash import sha256_file
from repository.comics import ComicsError, ComicsRepository
from repository.popularity import (AlreadyDiscardedError, CycleError, PopularityConflictError,
                                   PopularityRepository, WinnerGoneError)
from Storage.db import get_async_db
from Storage.models import (Comics, ComicsPanel, ComicsState, CutState, Image, ImageCut, ImageExtras,
                            ImageMetrics, ImageProcessingStatus, ImageStatus, PopularityAbsorption, PopularityKind,
                            ProcessEvent)

pytestmark = pytest.mark.asyncio(loop_scope="session")

SIZE = (40, 30)
QUALITY = 90
BUILT_NAME = re.compile(r"^comics-[0-9a-f]{12}\.(jpg|png)$")
GUARD = 2.0       # seconds a blocked build may take once the lock is released
BLOCKED_FOR = 0.5  # how long the losing build is watched to confirm it waits


# ---- helpers ----

def _color(seed: uuid.UUID) -> tuple[int, int, int]:
    b = seed.bytes
    return b[0], b[1], b[2]


def _write(path, fmt="JPEG", color=None, size=SIZE):
    PILImage.new("RGB", size, color or _color(uuid.uuid4())).save(path, format=fmt)


async def _panel_image(session, tmp_path, fmt="JPEG") -> Image:
    name = f"{uuid.uuid4()}.{'png' if fmt == 'PNG' else 'jpg'}"
    _write(tmp_path / name, fmt)
    img = Image(filename=name, status=ImageStatus.ACTIVE, width=SIZE[0], height=SIZE[1],
                content_hash=sha256_file(str(tmp_path / name)))
    session.add(img)
    await session.flush()
    return img


async def _comics(session, tmp_path, n, fmts=None):
    repo = ComicsRepository(session)
    comics = await repo.create(f"Build {uuid.uuid4()}")
    images, panels = [], []
    for i in range(n):
        img = await _panel_image(session, tmp_path, (fmts or ["JPEG"] * n)[i])
        images.append(img)
        panels.append(await repo.add_image(comics.id, img.id))
    return comics, images, panels


def _service(session, tmp_path):
    return ComicsBuildService(session, base_path=str(tmp_path), jpeg_quality=QUALITY)


def _files(tmp_path) -> list[str]:
    return sorted(p for p in os.listdir(tmp_path) if os.path.isfile(tmp_path / p))


async def _image(session, image_id) -> Image | None:
    return (await session.execute(select(Image).where(Image.id == image_id)
                                  .execution_options(populate_existing=True))).scalar_one_or_none()


async def _comics_row(session, comics_id) -> Comics:
    return (await session.execute(select(Comics).where(Comics.id == comics_id)
                                  .execution_options(populate_existing=True))).scalar_one()


async def _panel_rows(session, comics_id) -> list[ComicsPanel]:
    return list((await session.execute(
        select(ComicsPanel).where(ComicsPanel.comics_id == comics_id).order_by(ComicsPanel.position)
        .execution_options(populate_existing=True))).scalars().all())


async def _built_rows(session) -> list[Image]:
    return list((await session.execute(select(Image).where(Image.filename.like("comics-%")))).scalars().all())


async def _flag(session, image_id):
    row = (await session.execute(select(ImageExtras.flagged, ImageExtras.flagged_reason)
                                 .where(ImageExtras.image_id == image_id))).first()
    return (row.flagged, row.flagged_reason) if row is not None else (False, None)


async def _open_edges(session, **where):
    query = select(PopularityAbsorption).where(PopularityAbsorption.kind == PopularityKind.ABSORB,
                                               PopularityAbsorption.reversed_by_id.is_(None))
    for column, value in where.items():
        query = query.where(getattr(PopularityAbsorption, column) == value)
    return list((await session.execute(query)).scalars().all())


async def _assert_built_image(session, tmp_path, comics_id, built_id):
    built = await _image(session, built_id)
    comics = await _comics_row(session, comics_id)
    assert built.status == ImageStatus.ACTIVE
    assert BUILT_NAME.match(built.filename), built.filename
    path = tmp_path / built.filename
    assert built.content_hash == comics.built_content_hash == sha256_file(str(path))
    assert built.filename.startswith(f"comics-{built.content_hash[:12]}.")
    with PILImage.open(path) as im:
        assert (built.width, built.height) == im.size
        assert im.format == ("PNG" if built.filename.endswith(".png") else "JPEG")
    assert comics.state == ComicsState.BUILT and comics.built_image_id == built_id and comics.built_at is not None
    for model, column in ((ImageProcessingStatus, ImageProcessingStatus.image_id),
                          (ImageMetrics, ImageMetrics.image_id), (ProcessEvent, ProcessEvent.subject_id)):
        count = await session.scalar(select(func.count()).select_from(model).where(column == built_id))
        assert count == 0, model.__tablename__
    assert not [f for f in _files(tmp_path) if f.startswith(".comics-build-") or f.endswith(".tmp")]
    return built


def _id(obj) -> uuid.UUID:
    """The primary key of an ORM object without loading it (a rollback expires every object in the session)."""
    return sa_inspect(obj).identity[0]


async def _assert_unchanged_after_rollback(session, tmp_path, comics_id, images, files_before):
    """What every refused build must leave behind once get_async_db has rolled back."""
    image_ids = [_id(i) for i in images]
    assert _files(tmp_path) == files_before
    assert await _built_rows(session) == []
    comics = await _comics_row(session, comics_id)
    assert comics.state == ComicsState.DRAFT and comics.built_image_id is None and comics.built_content_hash is None
    panels = await _panel_rows(session, comics_id)
    assert [p.image_id for p in panels] == image_ids
    assert all(p.kept is False for p in panels)
    assert await _open_edges(session, source="comics_build") == []
    for image_id in image_ids:
        assert (await _flag(session, image_id))[1] != "comics_source"


async def _refused(session, tmp_path, comics, keep=()):
    with pytest.raises(ComicsError) as exc:
        await _service(session, tmp_path).build(_id(comics), list(keep))
    await session.rollback()  # what get_async_db does when the request raises
    return exc.value


# ---- successful builds ----

async def test_build_two_panels_unkept(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 2)

    result, built_id = await _service(db_session, tmp_path).build(comics.id, [])

    assert result.id == comics.id and result.state == ComicsState.BUILT and result.built_image_id == built_id
    built = await _assert_built_image(db_session, tmp_path, comics.id, built_id)
    assert built.filename.endswith(".jpg")
    assert built.popularity == 3
    panels = await _panel_rows(db_session, comics.id)
    assert [p.image_id for p in panels] == [i.id for i in images]  # kept until unregister deletes the rows
    assert all(p.kept is False for p in panels)
    for img in images:
        assert await _flag(db_session, img.id) == (True, "comics_source")
        assert (await _image(db_session, img.id)).popularity == 0
    for img in images:  # source files are never touched by the request
        assert (tmp_path / img.filename).exists()


async def test_build_three_panels_unkept(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3, fmts=["JPEG", "PNG", "JPEG"])

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [])

    built = await _assert_built_image(db_session, tmp_path, comics.id, built_id)
    assert built.filename.endswith(".png")
    assert built.popularity == 4
    assert len(await _open_edges(db_session, winner_image_id=built_id, source="comics_build")) == 3


async def test_build_with_one_kept_panel(db_session, tmp_path):
    comics, images, panels = await _comics(db_session, tmp_path, 3)

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [panels[1].id])

    built = await _assert_built_image(db_session, tmp_path, comics.id, built_id)
    assert built.popularity == 3
    rows = {p.id: p for p in await _panel_rows(db_session, comics.id)}
    assert rows[panels[1].id].kept is True and rows[panels[1].id].image_id is None
    assert rows[panels[1].id].source_image_id == images[1].id
    for i in (0, 2):
        assert rows[panels[i].id].kept is False and rows[panels[i].id].image_id == images[i].id
        assert await _flag(db_session, images[i].id) == (True, "comics_source")
    assert await _flag(db_session, images[1].id) == (False, None)


async def test_build_with_all_panels_kept(db_session, tmp_path):
    comics, images, panels = await _comics(db_session, tmp_path, 2)

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [p.id for p in panels])

    built = await _assert_built_image(db_session, tmp_path, comics.id, built_id)
    assert built.popularity == 1
    assert await _open_edges(db_session, winner_image_id=built_id) == []
    for p in await _panel_rows(db_session, comics.id):
        assert p.kept is True and p.image_id is None
    for img in images:
        assert await _flag(db_session, img.id) == (False, None)
        assert (await _image(db_session, img.id)).popularity == 1


async def test_stray_final_file_without_row_is_replaced(db_session, tmp_path):
    """An orphan comics-<hash12> file of an earlier crashed build (no row) is replaced by the new file."""
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    probe = tmp_path / "probe"
    probe.mkdir()
    composed = compose_to_file([str(tmp_path / i.filename) for i in images], str(probe / "x.jpg"), QUALITY)
    final = tmp_path / f"comics-{composed.content_hash[:12]}.jpg"
    final.write_bytes(b"half written")

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [])

    built = await _assert_built_image(db_session, tmp_path, comics.id, built_id)
    assert built.filename == final.name and built.content_hash == composed.content_hash


# ---- refusals before anything is written ----

async def test_second_build_is_built_error(db_session, tmp_path):
    comics, _, _ = await _comics(db_session, tmp_path, 2)
    await _service(db_session, tmp_path).build(comics.id, [])
    files_before = _files(tmp_path)

    with pytest.raises(ComicsError) as exc:
        await _service(db_session, tmp_path).build(comics.id, [])

    assert exc.value.code == "built"
    assert _files(tmp_path) == files_before
    assert len(await _built_rows(db_session)) == 1


async def test_not_found(db_session, tmp_path):
    with pytest.raises(ComicsError) as exc:
        await _service(db_session, tmp_path).build(uuid.uuid4(), [])
    assert exc.value.code == "not_found"


@pytest.mark.parametrize("n", [0, 1])
async def test_too_few_panels(db_session, tmp_path, n):
    comics, images, _ = await _comics(db_session, tmp_path, n)
    await db_session.commit()
    files_before = _files(tmp_path)

    error = await _refused(db_session, tmp_path, comics)

    assert error.code == "not_buildable" and error.detail == {"reason": "too_few_panels"}
    await _assert_unchanged_after_rollback(db_session, tmp_path, _id(comics), images, files_before)


async def test_panel_without_image_is_panel_missing(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    await db_session.execute(delete(Image).where(Image.id == images[1].id))  # SET NULL on the panel
    await db_session.commit()
    files_before = _files(tmp_path)

    error = await _refused(db_session, tmp_path, comics)

    assert error.code == "not_buildable" and error.detail == {"reason": "panel_missing"}
    assert _files(tmp_path) == files_before and await _built_rows(db_session) == []


async def test_keep_id_not_a_panel_is_keep_invalid(db_session, tmp_path):
    comics, images, panels = await _comics(db_session, tmp_path, 2)
    other, _, other_panels = await _comics(db_session, tmp_path, 2)
    await db_session.commit()
    files_before = _files(tmp_path)

    for keep in ([uuid.uuid4()], [panels[0].id, other_panels[0].id]):
        error = await _refused(db_session, tmp_path, comics, keep)
        assert error.code == "keep_invalid"
        await _assert_unchanged_after_rollback(db_session, tmp_path, _id(comics), images, files_before)


async def _assert_panel_unavailable(session, tmp_path, comics, images, cause, position):
    files_before = _files(tmp_path)
    error = await _refused(session, tmp_path, comics)
    assert error.code == "not_buildable"
    assert error.detail == {"reason": "panel_unavailable", "cause": cause, "position": position}
    await _assert_unchanged_after_rollback(session, tmp_path, _id(comics), images, files_before)


async def test_panel_unavailable_not_active(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    await db_session.execute(update(Image).where(Image.id == images[1].id).values(status=ImageStatus.PENDING))
    await db_session.commit()
    await _assert_panel_unavailable(db_session, tmp_path, comics, images, "not_active", 2)


async def test_panel_unavailable_flagged(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    await ImageRepository(db_session).set_flagged(images[2].id, True, "duplicate")
    await db_session.commit()
    await _assert_panel_unavailable(db_session, tmp_path, comics, images, "flagged", 3)


async def test_panel_unavailable_live_cut(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    db_session.add(ImageCut(source_image_id=images[0].id, source_content_hash=images[0].content_hash,
                            crop_x=0, crop_y=0, crop_w=10, crop_h=10, detector="margin_trim", detector_version="1",
                            state=CutState.DETECTED))
    await db_session.commit()
    await _assert_panel_unavailable(db_session, tmp_path, comics, images, "live_cut", 1)


async def test_panel_unavailable_file_missing(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    os.remove(tmp_path / images[1].filename)
    await db_session.commit()
    await _assert_panel_unavailable(db_session, tmp_path, comics, images, "file_missing", 2)


async def test_panel_unavailable_file_changed(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    _write(tmp_path / images[1].filename, color=(1, 2, 3), size=(50, 50))  # replaced on disk after it was added
    await db_session.commit()
    await _assert_panel_unavailable(db_session, tmp_path, comics, images, "file_changed", 2)


async def test_panel_unavailable_already_discarded(db_session, tmp_path):
    """The panel already has an open edge into another tree: detected at absorb time, after the file and row were
    written, so this also pins that the rollback removes them."""
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    keeper = await _panel_image(db_session, tmp_path)
    await PopularityRepository(db_session).absorb(images[1].id, keeper.id, "cluster_review")
    await db_session.commit()
    edges_before = len(await _open_edges(db_session))

    await _assert_panel_unavailable(db_session, tmp_path, comics, images, "already_discarded", 2)
    assert len(await _open_edges(db_session)) == edges_before
    assert (await _image(db_session, _id(keeper))).popularity == 2
    assert (await _image(db_session, _id(images[0]))).popularity == 1


# ---- refusals after composing ----

async def test_same_as_existing_refuses_and_keeps_the_existing_file(db_session, tmp_path):
    """An image row already holds the final name: the refusal deletes only the temporary file; the existing image's
    file (here with different bytes, so an overwrite would show) is untouched."""
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    probe = tmp_path / "probe"
    probe.mkdir()
    composed = compose_to_file([str(tmp_path / i.filename) for i in images], str(probe / "x.jpg"), QUALITY)
    final_name = f"comics-{composed.content_hash[:12]}.jpg"
    (tmp_path / final_name).write_bytes(b"the existing image's file")
    existing = Image(filename=final_name, status=ImageStatus.ACTIVE, content_hash="another-hash")
    db_session.add(existing)
    await db_session.flush()
    existing_id = existing.id
    await db_session.commit()
    files_before = _files(tmp_path)

    error = await _refused(db_session, tmp_path, comics)

    assert error.code == "not_buildable"
    assert error.detail == {"reason": "same_as_existing", "image_id": existing_id}
    assert (tmp_path / final_name).read_bytes() == b"the existing image's file"
    assert _files(tmp_path) == files_before
    assert (await _comics_row(db_session, _id(comics))).state == ComicsState.DRAFT


async def test_same_as_existing_by_content_hash(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    probe = tmp_path / "probe"
    probe.mkdir()
    composed = compose_to_file([str(tmp_path / i.filename) for i in images], str(probe / "x.jpg"), QUALITY)
    shutil.copy(probe / "x.jpg", tmp_path / "elsewhere.jpg")
    existing = Image(filename="elsewhere.jpg", status=ImageStatus.ACTIVE, content_hash=composed.content_hash)
    db_session.add(existing)
    await db_session.flush()
    existing_id = existing.id
    await db_session.commit()
    files_before = _files(tmp_path)

    error = await _refused(db_session, tmp_path, comics)

    assert error.code == "not_buildable"
    assert error.detail == {"reason": "same_as_existing", "image_id": existing_id}
    assert sha256_file(str(tmp_path / "elsewhere.jpg")) == composed.content_hash
    assert _files(tmp_path) == files_before
    assert not (tmp_path / f"comics-{composed.content_hash[:12]}.jpg").exists()


async def test_compose_failed_names_the_position(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    junk = tmp_path / images[1].filename
    junk.write_bytes(b"this is not an image")
    await db_session.execute(update(Image).where(Image.id == images[1].id)
                             .values(content_hash=sha256_file(str(junk))))  # the re-check passes, decoding fails
    await db_session.commit()
    files_before = _files(tmp_path)

    error = await _refused(db_session, tmp_path, comics)

    assert error.code == "compose_failed"
    assert error.detail == {"reason": "unreadable", "position": 2}
    await _assert_unchanged_after_rollback(db_session, tmp_path, _id(comics), images, files_before)


async def test_rollback_after_file_written_removes_file_and_leaves_comics_unchanged(db_session, tmp_path, monkeypatch):
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    await db_session.commit()
    files_before = _files(tmp_path)
    seen = {}

    async def broken(self, *args, **kwargs):
        seen["files"] = _files(tmp_path)
        raise RuntimeError("database trouble")
    monkeypatch.setattr(ComicsRepository, "mark_built", broken)

    with pytest.raises(RuntimeError):
        await _service(db_session, tmp_path).build(comics.id, [])
    written = [f for f in _files(tmp_path) if BUILT_NAME.match(f)]
    assert len(written) == 1 and written[0] in seen["files"]          # the file existed when the failure hit
    await db_session.rollback()

    await _assert_unchanged_after_rollback(db_session, tmp_path, _id(comics), images, files_before)


@pytest.mark.parametrize("error", [WinnerGoneError("gone"), CycleError("cycle"), PopularityConflictError("busy")],
                         ids=["winner_gone", "cycle", "conflict"])
async def test_popularity_conflict_aborts_everything(db_session, tmp_path, monkeypatch, error):
    comics, images, _ = await _comics(db_session, tmp_path, 2)
    await db_session.commit()
    files_before = _files(tmp_path)

    async def failing(self, *args, **kwargs):
        raise error
    monkeypatch.setattr(PopularityRepository, "absorb", failing)

    refused = await _refused(db_session, tmp_path, comics)

    assert refused.code == "popularity_conflict"
    await _assert_unchanged_after_rollback(db_session, tmp_path, _id(comics), images, files_before)


# ---- popularity and the panels' fate ----

async def test_built_popularity_is_one_plus_tree_weights(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 3)
    copy = await _panel_image(db_session, tmp_path)
    pop = PopularityRepository(db_session)
    await pop.absorb(copy.id, images[0].id, "cluster_review")       # panel 1 had absorbed a copy: popularity 2
    assert (await _image(db_session, images[0].id)).popularity == 2

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [])

    # 1 (own) + (1 + 1) for panel 1 with its copy + 1 + 1 for the other two panels, not 1 + number of panels
    assert (await _image(db_session, built_id)).popularity == 5
    assert await pop.popularity_of(built_id) == 5
    assert await pop.root_of(copy.id) == built_id
    assert await pop.verify([built_id, copy.id] + [i.id for i in images]) == []


async def test_kept_panels_keep_popularity_and_are_released_with_kept_true_and_never_flagged(db_session, tmp_path):
    comics, images, panels = await _comics(db_session, tmp_path, 3)
    copy = await _panel_image(db_session, tmp_path)
    pop = PopularityRepository(db_session)
    await pop.absorb(copy.id, images[2].id, "cluster_review")       # the kept panel has popularity 2

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [panels[2].id])

    assert (await _image(db_session, images[2].id)).popularity == 2
    assert (await _image(db_session, built_id)).popularity == 3
    assert await pop.root_of(images[2].id) == images[2].id
    assert await _open_edges(db_session, loser_image_id=images[2].id) == []
    assert await _flag(db_session, images[2].id) == (False, None)
    kept = next(p for p in await _panel_rows(db_session, comics.id) if p.id == panels[2].id)
    assert kept.kept is True and kept.image_id is None
    repo = ComicsRepository(db_session)
    again = await repo.create(f"Again {uuid.uuid4()}")
    assert (await repo.add_image(again.id, images[2].id)).image_id == images[2].id


async def test_unkept_panels_are_flagged_comics_source_and_absorbed_with_source_comics_build(db_session, tmp_path):
    comics, images, _ = await _comics(db_session, tmp_path, 2)

    _, built_id = await _service(db_session, tmp_path).build(comics.id, [])

    images_repo = ImageRepository(db_session)
    for img in images:
        assert await images_repo.get_flag_reason(img.id) == "comics_source"
        edges = await _open_edges(db_session, loser_image_id=img.id)
        assert len(edges) == 1
        edge = edges[0]
        assert (edge.source, edge.winner_image_id, edge.weight) == ("comics_build", built_id, 1)
        assert edge.loser_filename == img.filename and edge.loser_content_hash == img.content_hash


# ---- the failed commit and the concurrent builds (real, committed sessions) ----

async def _cleanup(engine, comics_ids, image_ids):
    async with AsyncSession(engine) as s:
        await s.execute(delete(PopularityAbsorption).where(or_(PopularityAbsorption.winner_image_id.in_(image_ids),
                                                               PopularityAbsorption.loser_image_id.in_(image_ids))))
        await s.execute(delete(Comics).where(Comics.id.in_(comics_ids)))
        await s.execute(delete(ImageExtras).where(ImageExtras.image_id.in_(image_ids)))
        await s.execute(delete(Image).where(Image.id.in_(image_ids)))
        await s.commit()


async def _committed_comics(engine, tmp_path, n):
    async with AsyncSession(engine, expire_on_commit=False) as s:
        comics, images, panels = await _comics(s, tmp_path, n)
        await s.commit()
        return comics.id, [i.id for i in images], [p.id for p in panels]


async def test_retry_after_failed_commit_reuses_the_same_file_name(db_engine, tmp_path):
    comics_id, image_ids, _ = await _committed_comics(db_engine, tmp_path, 2)
    # A second draft whose positions we break so that the deferred unique constraint fails at COMMIT time.
    blocker_id, blocker_images, blocker_panels = await _committed_comics(db_engine, tmp_path, 2)
    image_ids_all = list(image_ids) + list(blocker_images)
    try:
        files_before = _files(tmp_path)
        async with AsyncSession(db_engine, expire_on_commit=False) as s:
            _, first_built = await _service(s, tmp_path).build(comics_id, [])
            first_name = (await _image(s, first_built)).filename
            image_ids_all.append(first_built)
            assert (tmp_path / first_name).exists()
            await s.execute(update(ComicsPanel).where(ComicsPanel.id.in_(blocker_panels)).values(position=0))
            with pytest.raises(Exception):
                await s.commit()
            await s.rollback()  # what get_async_db does when the commit raises
        assert _files(tmp_path) == files_before
        async with AsyncSession(db_engine) as s:
            assert await _built_rows(s) == []
            assert (await _comics_row(s, comics_id)).state == ComicsState.DRAFT

        async with AsyncSession(db_engine, expire_on_commit=False) as s:
            _, built_id = await _service(s, tmp_path).build(comics_id, [])
            image_ids_all.append(built_id)
            await s.commit()
        async with AsyncSession(db_engine) as s:
            built = await _assert_built_image(s, tmp_path, comics_id, built_id)
            assert built.filename == first_name
    finally:
        await _cleanup(db_engine, [comics_id, blocker_id], image_ids_all)


async def test_concurrent_builds_exactly_one_wins(db_engine, tmp_path):
    comics_id, image_ids, _ = await _committed_comics(db_engine, tmp_path, 2)
    cleanup_ids = list(image_ids)
    try:
        async with AsyncSession(db_engine, expire_on_commit=False) as a, \
                AsyncSession(db_engine, expire_on_commit=False) as b:
            sessions = {}
            for s in (a, b):
                task = asyncio.ensure_future(_service(s, tmp_path).build(comics_id, []))
                sessions[task] = s
            done, pending = await asyncio.wait(set(sessions), timeout=30, return_when=asyncio.FIRST_COMPLETED)
            assert len(done) == 1, "both builds finished while one should wait on the comics row lock"
            winner = done.pop()
            loser = pending.pop()
            _, built_id = winner.result()
            cleanup_ids.append(built_id)
            await asyncio.sleep(BLOCKED_FOR)
            assert not loser.done(), "the second build proceeded while the first held the row lock"
            await sessions[winner].commit()
            with pytest.raises(ComicsError) as exc:
                await asyncio.wait_for(loser, GUARD)
            assert exc.value.code == "built"
            await sessions[loser].rollback()

        async with AsyncSession(db_engine) as s:
            assert [i.id for i in await _built_rows(s)] == [built_id]
            await _assert_built_image(s, tmp_path, comics_id, built_id)
            for image_id in image_ids:
                assert len(await _open_edges(s, loser_image_id=image_id)) == 1
            assert len(await _open_edges(s, winner_image_id=built_id)) == 2
        assert len([f for f in _files(tmp_path) if BUILT_NAME.match(f)]) == 1
    finally:
        await _cleanup(db_engine, [comics_id], cleanup_ids)


async def test_concurrent_builds_with_identical_output_one_wins_the_other_is_same_as_existing(
        db_engine, tmp_path, monkeypatch):
    """Two different comics (different panels, so no shared row lock) whose composed output is byte-identical: the
    advisory lock on the content hash makes the second wait for the first's commit and then see its row (D12(b))."""
    first_id, first_images, _ = await _committed_comics(db_engine, tmp_path, 2)
    second_id, second_images, _ = await _committed_comics(db_engine, tmp_path, 2)
    cleanup_ids = list(first_images) + list(second_images)
    async with AsyncSession(db_engine) as s:
        fixed = [str(tmp_path / (await _image(s, i)).filename) for i in first_images]
    real = comics_build_service._compose_to_temp
    # Both builds compose the first comics' panels, so their bytes (and content hash) are the same.
    monkeypatch.setattr(comics_build_service, "_compose_to_temp", lambda paths, base, q: real(fixed, base, q))
    try:
        async with AsyncSession(db_engine, expire_on_commit=False) as a, \
                AsyncSession(db_engine, expire_on_commit=False) as b:
            sessions = {}
            for s, comics_id in ((a, first_id), (b, second_id)):
                task = asyncio.ensure_future(_service(s, tmp_path).build(comics_id, []))
                sessions[task] = (s, comics_id)
            done, pending = await asyncio.wait(set(sessions), timeout=30, return_when=asyncio.FIRST_COMPLETED)
            assert len(done) == 1, "both builds finished while one should wait on the content-hash lock"
            winner, loser = done.pop(), pending.pop()
            _, built_id = winner.result()
            cleanup_ids.append(built_id)
            await asyncio.sleep(BLOCKED_FOR)
            assert not loser.done(), "the second build proceeded while the first held the content-hash lock"
            await sessions[winner][0].commit()
            with pytest.raises(ComicsError) as exc:
                await asyncio.wait_for(loser, GUARD)
            assert exc.value.code == "not_buildable"
            assert exc.value.detail == {"reason": "same_as_existing", "image_id": built_id}
            await sessions[loser][0].rollback()

        async with AsyncSession(db_engine) as s:
            assert [i.id for i in await _built_rows(s)] == [built_id]
            await _assert_built_image(s, tmp_path, sessions[winner][1], built_id)
            assert (await _comics_row(s, sessions[loser][1])).state == ComicsState.DRAFT
        assert len([f for f in _files(tmp_path) if BUILT_NAME.match(f)]) == 1
    finally:
        await _cleanup(db_engine, [first_id, second_id], cleanup_ids)


# ---- the endpoint starts comics_pipeline only after the build is committed (C5) ----

def _build_app(tmp_path):
    """The real router with the real get_async_db (the test database); only the base path is the test's."""
    async def build_service(db: AsyncSession = Depends(get_async_db)):
        yield ComicsBuildService(db, base_path=str(tmp_path), jpeg_quality=QUALITY)
    app = FastAPI()
    app.include_router(comics_router, prefix="/api")
    app.dependency_overrides[get_comics_build_service] = build_service
    return app


async def _post_build(tmp_path, comics_id):
    transport = httpx.ASGITransport(app=_build_app(tmp_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(f"/api/comics/{comics_id}/build", json={})


async def test_endpoint_starts_the_pipeline_after_the_built_row_is_committed(db_engine, tmp_path, monkeypatch):
    comics_id, image_ids, _ = await _committed_comics(db_engine, tmp_path, 2)
    cleanup_ids = list(image_ids)
    seen = []

    async def trigger():
        # What the pipeline's subprocess would see: a second session, not the request's.
        async with AsyncSession(db_engine) as other:
            built_id = (await _comics_row(other, comics_id)).built_image_id
            built = await _image(other, built_id) if built_id is not None else None
            seen.append(None if built is None else (built.id, built.status))
        return "run-id"
    monkeypatch.setattr(comics_api, "start_comics_pipeline", trigger)
    try:
        response = await _post_build(tmp_path, comics_id)
        assert response.status_code == 200, response.text
        built_id = uuid.UUID(response.json()["builtImageId"])
        cleanup_ids.append(built_id)
        assert seen == [(built_id, ImageStatus.ACTIVE)]
    finally:
        await _cleanup(db_engine, [comics_id], cleanup_ids)


async def test_endpoint_refused_build_does_not_start_the_pipeline(db_engine, tmp_path, monkeypatch):
    comics_id, image_ids, _ = await _committed_comics(db_engine, tmp_path, 1)
    trigger = AsyncMock()
    monkeypatch.setattr(comics_api, "start_comics_pipeline", trigger)
    try:
        response = await _post_build(tmp_path, comics_id)
        assert response.status_code == 409 and response.json()["detail"]["reason"] == "too_few_panels"
        trigger.assert_not_awaited()
    finally:
        await _cleanup(db_engine, [comics_id], list(image_ids))
