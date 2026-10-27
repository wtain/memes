"""OCR refresh pieces (screenshot recut R3c): selection, the file producer and the per-image commit hook.
The GPU pipeline itself is not run here. Requires a live PostgreSQL instance -- see conftest."""
import asyncio
import types
import uuid

import pytest
from sqlalchemy import select

from batch.utils.batch_commit import BatchCommitter
from batch.utils.ocr_refresh import MAX_CONSECUTIVE_UNREADABLE, refresh_producer, select_refresh_images
from Storage.models import (
    Image, ImageClassification, ImageStatus, OCR_TEXT_EMBEDDING_DIM, OCRText, OCRTextEmbedding,
)

TIMINGS = {"read_time_ms": 0, "preprocess_time_ms": 0, "ocr_time_ms": 0, "total_time_ms": 0}


class Counter:
    def __init__(self):
        self.counts = {}

    def increment(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1


class Tracker:
    skipped = 0

    def skip(self):
        self.skipped += 1


async def _image(session, status=ImageStatus.CUT_PROPOSED):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image


@pytest.mark.asyncio(loop_scope="session")
async def test_select_returns_only_cut_images_with_stale_ocr(db_session):
    stale_cut = await _image(db_session)
    fresh_cut = await _image(db_session)
    stale_active = await _image(db_session, ImageStatus.ACTIVE)
    db_session.add_all([OCRText(image_id=stale_cut.id, text="a", stale=True),
                        OCRText(image_id=fresh_cut.id, text="b", stale=False),
                        OCRText(image_id=stale_active.id, text="c", stale=True)])
    await db_session.flush()

    ids = [image.id for image in await select_refresh_images(db_session)]

    assert stale_cut.id in ids and fresh_cut.id not in ids and stale_active.id not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_producer_reads_files_and_ends_the_queue(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"data")
    image = types.SimpleNamespace(filename="a.jpg")
    queue = asyncio.Queue()

    await refresh_producer(str(tmp_path), [image], queue, Counter(), Tracker())

    file, data, _read_seconds, item = await queue.get()
    assert (file, data, item) == ("a.jpg", b"data", image)
    assert await queue.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_producer_counts_unreadable_files_and_stops_after_twenty_in_a_row(tmp_path):
    images = [types.SimpleNamespace(filename=f"missing{i}.jpg") for i in range(MAX_CONSECUTIVE_UNREADABLE + 5)]
    queue = asyncio.Queue()
    metrics, tracker = Counter(), Tracker()

    with pytest.raises(RuntimeError):
        await refresh_producer(str(tmp_path), images, queue, metrics, tracker)

    assert metrics.counts["error.reading"] == MAX_CONSECUTIVE_UNREADABLE
    assert await queue.get() is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_unreadable_file_between_good_ones_does_not_stop_the_run(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"1")
    (tmp_path / "c.jpg").write_bytes(b"3")
    images = [types.SimpleNamespace(filename=name) for name in ("a.jpg", "b.jpg", "c.jpg")]
    queue = asyncio.Queue()
    metrics = Counter()

    await refresh_producer(str(tmp_path), images, queue, metrics, Tracker())

    names = [(await queue.get())[0], (await queue.get())[0]]
    assert names == ["a.jpg", "c.jpg"] and metrics.counts["error.reading"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_refresh_done_clears_what_was_computed_from_the_old_text(db_session):
    cut = await _image(db_session)
    other = await _image(db_session)
    db_session.add_all([
        OCRText(image_id=cut.id, text="old", language="ru", stale=True),
        ImageClassification(image_id=cut.id, classifier="text_heavy", result="text_heavy"),
        OCRTextEmbedding(image_id=cut.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        ImageClassification(image_id=other.id, classifier="text_heavy", result="text_heavy"),
    ])
    await db_session.flush()

    committer = BatchCommitter(db_session, batch_size=1000, pipeline="easyocr:en")
    await committer.on_refresh_done(cut, TIMINGS)

    assert (await db_session.execute(select(OCRText).where(OCRText.image_id == cut.id))).scalars().all() == []
    assert (await db_session.execute(
        select(ImageClassification).where(ImageClassification.image_id == cut.id))).scalars().all() == []
    assert (await db_session.execute(
        select(OCRTextEmbedding).where(OCRTextEmbedding.image_id == cut.id))).scalars().all() == []
    assert (await db_session.execute(
        select(ImageClassification).where(ImageClassification.image_id == other.id))).scalars().all() != []
