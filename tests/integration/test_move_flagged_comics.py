"""move_flagged routes comics_source images to BASE_PATH/comics-sources/<comics name>/ (C4); everything else still
goes to excluded/. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

import batch.move_flagged as move_flagged
from repository.comics_name import name_key_of, normalize_name
from Storage.models import Comics, ComicsPanel, Image, ImageExtras, ImageStatus


async def _flagged_image(db_session, tmp_path, reason, content=b"x", filename=None):
    image = Image(filename=filename or f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    db_session.add(image)
    await db_session.flush()
    (tmp_path / image.filename).write_bytes(content)
    db_session.add(ImageExtras(image_id=image.id, flagged=True, flagged_reason=reason))
    await db_session.flush()
    return image


async def _comics_with_panel(db_session, name, image, position=0):
    n, key = normalize_name(name)
    comics = Comics(name=n, name_key=key)
    db_session.add(comics)
    await db_session.flush()
    db_session.add(ComicsPanel(comics_id=comics.id, image_id=image.id, position=position,
                               source_image_id=image.id, source_filename=image.filename))
    await db_session.flush()
    return comics


@pytest.mark.asyncio(loop_scope="session")
async def test_comics_source_goes_to_comics_sources_folder(db_session, tmp_path):
    image = await _flagged_image(db_session, tmp_path, "comics_source")
    await _comics_with_panel(db_session, f"Strip {uuid.uuid4().hex[:8]}", image)
    name = (await db_session.get(Comics, (await _panel_comics_id(db_session, image)))).name

    metrics = await move_flagged.run(db_session, str(tmp_path))

    assert (tmp_path / "comics-sources" / name / image.filename).exists()
    assert not (tmp_path / "excluded" / image.filename).exists()
    assert not (tmp_path / image.filename).exists()
    counters = metrics.counters_dict()
    assert counters.get("moved_to_comics_sources") == 1
    assert counters.get("moved") == 1


async def _panel_comics_id(db_session, image):
    from sqlalchemy import select
    return (await db_session.execute(
        select(ComicsPanel.comics_id).where(ComicsPanel.image_id == image.id))).scalar_one()


@pytest.mark.asyncio(loop_scope="session")
async def test_ordinary_flagged_image_still_goes_to_excluded(db_session, tmp_path):
    image = await _flagged_image(db_session, tmp_path, "duplicate_review")

    metrics = await move_flagged.run(db_session, str(tmp_path))

    assert (tmp_path / "excluded" / image.filename).exists()
    assert not (tmp_path / "comics-sources").exists()
    assert metrics.counters_dict().get("moved_to_comics_sources", 0) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_comics_source_without_panel_falls_back_to_excluded(db_session, tmp_path):
    image = await _flagged_image(db_session, tmp_path, "comics_source")

    metrics = await move_flagged.run(db_session, str(tmp_path))

    assert (tmp_path / "excluded" / image.filename).exists()
    assert metrics.counters_dict().get("moved_to_comics_sources", 0) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_existing_file_in_target_folder_is_not_overwritten(db_session, tmp_path):
    image = await _flagged_image(db_session, tmp_path, "comics_source", content=b"new")
    comics = await _comics_with_panel(db_session, f"Strip {uuid.uuid4().hex[:8]}", image)
    target = tmp_path / "comics-sources" / comics.name
    target.mkdir(parents=True)
    (target / image.filename).write_bytes(b"old")

    metrics = await move_flagged.run(db_session, str(tmp_path))

    assert (target / image.filename).read_bytes() == b"old"
    stem, ext = image.filename.rsplit(".", 1)
    assert (target / f"{stem}_1.{ext}").read_bytes() == b"new"
    assert metrics.counters_dict().get("renamed_to_avoid_overwrite") == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_cyrillic_comics_name(db_session, tmp_path):
    image = await _flagged_image(db_session, tmp_path, "comics_source")
    suffix = uuid.uuid4().hex[:6]
    comics = await _comics_with_panel(db_session, f"Мой комикс {suffix}", image)

    await move_flagged.run(db_session, str(tmp_path))

    assert (tmp_path / "comics-sources" / f"Мой комикс {suffix}" / image.filename).exists()
    assert comics.name == f"Мой комикс {suffix}"


@pytest.mark.asyncio(loop_scope="session")
async def test_names_differing_only_in_case_cannot_coexist_and_folders_do_not_mix(db_session, tmp_path):
    suffix = uuid.uuid4().hex[:6]
    upper, lower = f"CASE {suffix}", f"case {suffix}"
    assert name_key_of(upper) == name_key_of(lower)

    first = await _flagged_image(db_session, tmp_path, "comics_source")
    await _comics_with_panel(db_session, upper, first)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(Comics(name=lower, name_key=name_key_of(lower)))
            await db_session.flush()

    second = await _flagged_image(db_session, tmp_path, "comics_source")
    await _comics_with_panel(db_session, f"other {suffix}", second)

    await move_flagged.run(db_session, str(tmp_path))

    assert (tmp_path / "comics-sources" / upper / first.filename).exists()
    assert (tmp_path / "comics-sources" / f"other {suffix}" / second.filename).exists()
    assert not (tmp_path / "comics-sources" / upper / second.filename).exists()
