"""
End-to-end paging of a ranked search through the real path: ImageService.search -> JSON cursor ->
ImageRepository. Requires a live PostgreSQL instance -- see tests/integration/conftest.py.

_fill_texts_and_tags runs for real against the repository (it only reads OCR text/tag rows). The only
stub is ImageService._record_history, which opens its own AsyncSessionLocal session outside the test
transaction.
"""
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_service import ImageService
from Storage.models import DescriptionNoteLemma, Image, OCRLemma

_T0 = datetime(2026, 1, 1, 12, 0, 0)
_WORD = "zebracorn"


async def _image(db_session, minutes):
    image = Image(filename=f"{uuid.uuid4()}.jpg", created_at=_T0 + timedelta(minutes=minutes))
    db_session.add(image)
    await db_session.flush()
    return image


def _service(db_session, monkeypatch):
    monkeypatch.setattr(ImageService, "_record_history", AsyncMock())
    return ImageService(ImageRepository(db_session), AsyncMock(), AsyncMock())


async def _page_through(service, limit):
    pages, cursor = [], None
    while True:
        page = await service.search(q=_WORD, raw_facets=None, cursor=cursor, limit=limit)
        pages.append(page)
        if not page.hasNext:
            return pages
        assert page.nextCursor
        cursor = page.nextCursor
        assert len(pages) < 20, "paging did not terminate"


@pytest.mark.asyncio(loop_scope="session")
async def test_paging_visits_every_match_once_in_relevance_order(db_session, monkeypatch):
    images = [await _image(db_session, minutes=m) for m in range(7)]
    note_idx = {1, 4}
    for i, image in enumerate(images):
        model = DescriptionNoteLemma if i in note_idx else OCRLemma
        db_session.add(model(image_id=image.id, lemma=_WORD))
    await db_session.flush()
    # note hits (1.0) first, newest first; then ocr hits (0.9), newest first
    expected = [str(images[i].id) for i in (4, 1, 6, 5, 3, 2, 0)]

    pages = await _page_through(_service(db_session, monkeypatch), limit=3)

    got = [item.id for page in pages for item in page.items]
    assert got == expected
    assert len(set(got)) == 7
    assert [p.hasNext for p in pages] == [True, True, False]
    assert [len(p.items) for p in pages] == [3, 3, 1]


@pytest.mark.asyncio(loop_scope="session")
async def test_match_count_equal_to_limit_has_no_next_page(db_session, monkeypatch):
    images = [await _image(db_session, minutes=m) for m in range(3)]
    for image in images:
        db_session.add(OCRLemma(image_id=image.id, lemma=_WORD))
    await db_session.flush()

    page = await _service(db_session, monkeypatch).search(q=_WORD, raw_facets=None, cursor=None, limit=3)

    assert page.hasNext is False
    assert [item.id for item in page.items] == [str(images[i].id) for i in (2, 1, 0)]
