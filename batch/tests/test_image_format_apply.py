"""
Unit tests for batch/utils/image_format_apply.py -- the shared per-image orchestration both
ingest_validate_formats.py and fix_image_formats.py use. Repos are AsyncMock'd (no DB),
matching batch/tests/test_move_flagged.py's style; the filesystem side uses real tmp_path
files, matching batch/tests/test_image_format_fix.py.
"""
import os
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image as PILImage

from batch.utils.image_format_apply import apply_format_fix
from batch.utils.image_format_fix import FixOutcome, fix_image_file as real_fix_image_file
from metrics.listener import SimpleMetricsListener


def _save(base_path, filename: str, pillow_format: str) -> None:
    PILImage.new("RGB", (4, 4), (255, 0, 0)).save(os.path.join(str(base_path), filename), pillow_format)


@pytest.mark.asyncio
async def test_noop_is_counted_and_touches_neither_repo(tmp_path):
    _save(tmp_path, "a.jpg", "JPEG")
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()

    await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.jpg")

    assert metrics.counters_dict() == {"no_op": 1}
    images_repo.update_filename_and_hash.assert_not_awaited()
    extras_repo.set_flagged.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_failure_is_isolated_and_does_not_abort_the_run(tmp_path):
    """The critical guarantee: a mid-run filesystem/Pillow failure on one image must not
    propagate out and abort the whole batch (which would roll back the DB while leaving
    already-performed renames on disk), and must not touch the DB session at all."""
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()

    with patch(
        "batch.utils.image_format_apply.fix_image_file", side_effect=OSError("disk exploded"),
    ):
        await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.jpg")

    assert metrics.counters_dict() == {"error.fix_failed": 1}
    images_repo.update_filename_and_hash.assert_not_awaited()
    extras_repo.set_flagged.assert_not_awaited()


@pytest.mark.asyncio
async def test_fix_failure_does_not_stop_later_images_in_the_same_loop(tmp_path):
    """Same guarantee, viewed from the caller's loop: the next image is still processed."""
    _save(tmp_path, "b.jpg", "JPEG")
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()
    def flaky(base_path, filename):
        if filename == "a.jpg":
            raise OSError("disk exploded")
        return real_fix_image_file(base_path, filename)

    with patch("batch.utils.image_format_apply.fix_image_file", side_effect=flaky):
        for filename in ("a.jpg", "b.jpg"):
            await apply_format_fix(
                images_repo, extras_repo, metrics, str(tmp_path), "img", filename,
            )

    assert metrics.counters_dict() == {"error.fix_failed": 1, "no_op": 1}


@pytest.mark.asyncio
async def test_unreadable_flags_the_image(tmp_path):
    (tmp_path / "broken.jpg").write_bytes(b"not an image")
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()

    await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "broken.jpg")

    assert metrics.counters_dict() == {"unreadable": 1}
    extras_repo.set_flagged.assert_awaited_once_with(
        "img-1", True, remarks="unreadable during format validation",
    )
    images_repo.update_filename_and_hash.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_persists_the_new_filename_without_a_hash(tmp_path):
    _save(tmp_path, "a.jpg", "PNG")
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()

    await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.jpg")

    assert metrics.counters_dict() == {"renamed": 1}
    images_repo.update_filename_and_hash.assert_awaited_once_with(
        "img-1", "a.png", content_hash=None,
    )
    extras_repo.set_flagged.assert_not_awaited()


@pytest.mark.asyncio
async def test_conversion_persists_the_new_filename_and_hash(tmp_path):
    _save(tmp_path, "a.webp", "WEBP")
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()

    await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.webp")

    assert metrics.counters_dict() == {"converted": 1}
    (args, kwargs) = images_repo.update_filename_and_hash.await_args
    assert args == ("img-1", "a.jpg")
    assert kwargs["content_hash"]  # a real sha256 of the newly written jpeg


@pytest.mark.asyncio
async def test_animated_conversion_gets_its_own_counter(tmp_path):
    """An animated source is flattened to its first frame -- counted separately so an
    operator can see from the run's stats that animation was lost."""
    images_repo, extras_repo = AsyncMock(), AsyncMock()
    metrics = SimpleMetricsListener()
    outcome = FixOutcome(
        changed=True, new_filename="a.jpg", new_content_hash="deadbeef", animated=True,
    )

    with patch("batch.utils.image_format_apply.fix_image_file", return_value=outcome):
        await apply_format_fix(images_repo, extras_repo, metrics, str(tmp_path), "img-1", "a.webp")

    assert metrics.counters_dict() == {"converted_animated": 1}
    images_repo.update_filename_and_hash.assert_awaited_once_with(
        "img-1", "a.jpg", content_hash="deadbeef",
    )
