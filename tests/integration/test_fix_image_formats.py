"""
Integration tests for batch/fix_image_formats.py (retroactive format-fix maintenance
batch).

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py.
Filesystem operations use pytest's tmp_path, standing in for BASE_PATH.
"""
import os

import pytest
from PIL import Image as PILImage
from sqlalchemy import select

from batch.fix_image_formats import run
from Storage.models import Image, ImageExtras


def _save(base_path, filename: str, pillow_format: str) -> None:
    PILImage.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(str(base_path), filename), pillow_format)


@pytest.mark.asyncio(loop_scope="session")
async def test_fixes_active_images_by_default(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", content_hash="orig")
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "PNG")

    metrics = await run(db_session, str(tmp_path), "active")

    assert metrics.counters_dict() == {"renamed": 1}
    refreshed = await db_session.get(Image, image.id)
    assert refreshed.filename == "a.png"


@pytest.mark.asyncio(loop_scope="session")
async def test_ignores_pending_images_when_status_is_active(tmp_path, db_session):
    pending_image = Image(filename="a.jpg", status="pending")
    db_session.add(pending_image)
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), "active")

    assert metrics.counters_dict() == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_status_flag_can_target_pending_images(tmp_path, db_session):
    pending_image = Image(filename="a.jpg", status="pending", content_hash="orig")
    db_session.add(pending_image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "WEBP")

    metrics = await run(db_session, str(tmp_path), "pending")

    assert metrics.counters_dict() == {"converted": 1}
    refreshed = await db_session.get(Image, pending_image.id)
    assert refreshed.filename == "a.jpg"
    assert refreshed.content_hash != "orig"


@pytest.mark.asyncio(loop_scope="session")
async def test_flags_unreadable_active_image(tmp_path, db_session):
    image = Image(filename="broken.jpg", status="active")
    db_session.add(image)
    await db_session.flush()
    (tmp_path / "broken.jpg").write_bytes(b"not an image")

    metrics = await run(db_session, str(tmp_path), "active")

    assert metrics.counters_dict() == {"unreadable": 1}
    extras = (await db_session.execute(
        select(ImageExtras).where(ImageExtras.image_id == image.id)
    )).scalar_one()
    assert extras.flagged is True
    assert extras.remarks == "unreadable during format validation"


@pytest.mark.asyncio(loop_scope="session")
async def test_rerun_is_a_noop_on_already_fixed_images(tmp_path, db_session):
    image = Image(filename="a.jpg", status="active", content_hash="orig")
    db_session.add(image)
    await db_session.flush()
    _save(tmp_path, "a.jpg", "PNG")

    first = await run(db_session, str(tmp_path), "active")
    assert first.counters_dict() == {"renamed": 1}

    second = await run(db_session, str(tmp_path), "active")
    assert second.counters_dict() == {"no_op": 1}
