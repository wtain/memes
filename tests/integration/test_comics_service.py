import uuid

import pytest

from Backend.app.services.comics_service import ComicsService
from Backend.app.types.generated.comicsdetail import Schema as ComicsDetailSchema
from Backend.app.types.generated.comicspage import Schema as ComicsPageSchema
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
    for item in page["items"]:
        if item["id"] in first_images:
            assert item["state"] == "draft" and item["coverImageId"] == first_images[item["id"]]

    source, target = created[0], created[1]
    moved = uuid.UUID(first_images[source["id"]])
    detail = await service.add_panel(uuid.UUID(target["id"]), moved, move=True)
    validated = ComicsDetailSchema.model_validate(detail)
    assert [p.imageId for p in validated.panels] == [first_images[target["id"]], str(moved)]
    assert [p.position for p in validated.panels] == [0, 1]
    assert (await service.detail(uuid.UUID(source["id"])))["panels"] == []
