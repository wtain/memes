import uuid

import pytest
from PIL import Image as PILImage

from Backend.app.services.comics_service import ComicsService, compose_http_error
from Backend.app.types.generated.comicsdetail import Schema as ComicsDetailSchema
from Backend.app.types.generated.comicspage import Schema as ComicsPageSchema
from batch.utils.comics_compose import ComposeError
from Storage.models import Image


async def _image(s):
    img = Image(filename=f"{uuid.uuid4()}.png", status="active", width=100, height=100, content_hash=str(uuid.uuid4()))
    s.add(img)
    await s.flush()
    return img


@pytest.mark.asyncio(loop_scope="session")
async def test_list_pages_with_covers_and_add_panel_moves_between_comics(db_session):
    service = ComicsService(db_session)
    created = [await service.create(f"Service {i} {uuid.uuid4()}") for i in range(3)]
    first_images = {}
    for c in created:
        image = await _image(db_session)
        first_images[c["id"]] = str(image.id)
        await service.add_panel(uuid.UUID(c["id"]), image.id, move=False)

    page = await service.list(None, 2, 0)
    ComicsPageSchema.model_validate(page)
    assert len(page["items"]) == 2 and page["hasMore"] is True
    checked = 0
    for item in page["items"]:
        if item["id"] in first_images:
            assert item["state"] == "draft" and item["coverImageId"] == first_images[item["id"]]
            checked += 1
    assert checked >= 1

    source, target = created[0], created[1]
    moved = uuid.UUID(first_images[source["id"]])
    detail = await service.add_panel(uuid.UUID(target["id"]), moved, move=True)
    validated = ComicsDetailSchema.model_validate(detail)
    assert [p.imageId for p in validated.panels] == [first_images[target["id"]], str(moved)]
    assert [p.position for p in validated.panels] == [0, 1]
    assert (await service.detail(uuid.UUID(source["id"])))["panels"] == []


async def _png_image(s, directory, size=(40, 30)):
    filename = f"{uuid.uuid4()}.png"
    PILImage.new("RGB", size, "red").save(directory / filename)
    img = Image(filename=filename, status="active", width=size[0], height=size[1], content_hash=str(uuid.uuid4()))
    s.add(img)
    await s.flush()
    return img


@pytest.mark.asyncio(loop_scope="session")
async def test_preview_composes_real_files_and_reports_a_missing_file_by_position(db_session, tmp_path):
    service = ComicsService(db_session, base_path=str(tmp_path))
    comics = await service.create(f"Preview {uuid.uuid4()}")
    comics_id = uuid.UUID(comics["id"])
    first = await _png_image(db_session, tmp_path)
    second = await _png_image(db_session, tmp_path)
    await service.add_panel(comics_id, first.id, move=False)
    await service.add_panel(comics_id, second.id, move=False)

    data = await service.preview(comics_id, 200)
    assert data.startswith(b"\xff\xd8")

    (tmp_path / second.filename).unlink()
    with pytest.raises(ComposeError) as raised:
        await service.preview(comics_id, 200)
    assert raised.value.reason == "unreadable"
    assert raised.value.position == 1  # 0-based: the second panel
    http = compose_http_error(raised.value)
    assert http.status_code == 422
    assert http.detail == {"code": "compose_failed", "reason": "unreadable", "position": 2}  # 1-based
