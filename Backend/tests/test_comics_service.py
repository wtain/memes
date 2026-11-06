import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Backend.app.services.comics_service import ComicsService, http_error
from repository.comics import Addability, ComicsError

NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def panel(position, image_id):
    return SimpleNamespace(id=uuid.uuid4(), position=position, image_id=image_id, source_filename=f"{position}.jpg",
                           source_image_id=image_id or uuid.uuid4(), kept=False)


def comics(name="C", state="draft", panels=(), built_image_id=None):
    return SimpleNamespace(id=uuid.uuid4(), name=name, state=state, panels=list(panels), created_at=NOW,
                           built_at=NOW if built_image_id else None, built_image_id=built_image_id)


def make_service(repo):
    service = ComicsService(session=None)
    service.repo = repo
    return service


async def test_cover_is_first_panel_by_position_for_a_draft():
    first, second = uuid.uuid4(), uuid.uuid4()
    repo = SimpleNamespace(search=AsyncMock(return_value=[comics(panels=[panel(1, second), panel(0, first)])]))
    page = await make_service(repo).list(None, 10, 0)
    assert page["items"][0]["coverImageId"] == str(first) and page["items"][0]["panelCount"] == 2


async def test_cover_is_built_image_for_a_built_comics():
    built = uuid.uuid4()
    repo = SimpleNamespace(search=AsyncMock(return_value=[
        comics(state="built", panels=[panel(0, uuid.uuid4())], built_image_id=built)]))
    page = await make_service(repo).list(None, 10, 0)
    assert page["items"][0]["coverImageId"] == str(built) and page["items"][0]["builtAt"] is not None


async def test_cover_is_none_when_first_panel_is_retired_or_no_panels():
    repo = SimpleNamespace(search=AsyncMock(return_value=[
        comics(panels=[panel(0, None)]), comics()]))
    items = (await make_service(repo).list(None, 10, 0))["items"]
    assert [i["coverImageId"] for i in items] == [None, None]
    assert [i["panelCount"] for i in items] == [1, 0]


async def test_has_more_fetches_one_extra_row():
    repo = SimpleNamespace(search=AsyncMock(return_value=[comics(), comics(), comics()]))
    page = await make_service(repo).list("q", 2, 4)
    repo.search.assert_awaited_once_with("q", 3, 4)
    assert len(page["items"]) == 2 and page["hasMore"] is True


async def test_has_more_false_at_exactly_limit():
    repo = SimpleNamespace(search=AsyncMock(return_value=[comics(), comics()]))
    page = await make_service(repo).list(None, 2, 0)
    assert len(page["items"]) == 2 and page["hasMore"] is False


async def test_add_panel_uses_move_or_add():
    c = comics(panels=[panel(0, uuid.uuid4())])
    repo = SimpleNamespace(add_image=AsyncMock(), move_image=AsyncMock(), get=AsyncMock(return_value=c))
    service = make_service(repo)
    image = uuid.uuid4()
    detail = await service.add_panel(c.id, image, False)
    repo.add_image.assert_awaited_once_with(c.id, image)
    repo.move_image.assert_not_awaited()
    assert detail["panels"][0]["position"] == 0
    await service.add_panel(c.id, image, True)
    repo.move_image.assert_awaited_once_with(image, c.id)


async def test_remove_panel_of_another_comics_is_not_found():
    c = comics(panels=[panel(0, uuid.uuid4())])
    repo = SimpleNamespace(get=AsyncMock(return_value=c), remove_panel=AsyncMock())
    with pytest.raises(ComicsError) as caught:
        await make_service(repo).remove_panel(c.id, uuid.uuid4())
    assert caught.value.code == "not_found"
    repo.remove_panel.assert_not_awaited()


async def test_remove_panel_calls_repository():
    p = panel(0, uuid.uuid4())
    c = comics(panels=[p])
    repo = SimpleNamespace(get=AsyncMock(return_value=c), remove_panel=AsyncMock())
    await make_service(repo).remove_panel(c.id, p.id)
    repo.remove_panel.assert_awaited_once_with(p.id)


async def test_addability_maps_items():
    a, b = uuid.uuid4(), uuid.uuid4()
    owner = uuid.uuid4()
    repo = SimpleNamespace(addability=AsyncMock(return_value={
        a: Addability(a, True, None), b: Addability(b, False, "in_comics", owner, "Story")}))
    items = (await make_service(repo).addability([a, b]))["items"]
    assert items[1] == {"imageId": str(b), "addable": False, "reason": "in_comics",
                        "comicsId": str(owner), "comicsName": "Story"}
    assert items[0]["comicsId"] is None


async def test_by_image_returns_zero_based_position_or_null():
    c = comics(panels=[panel(2, uuid.uuid4())])
    repo = SimpleNamespace(find_by_image=AsyncMock(return_value=(c, c.panels[0])))
    service = make_service(repo)
    assert (await service.by_image(uuid.uuid4()))["comics"]["position"] == 2
    repo.find_by_image.return_value = None
    assert await service.by_image(uuid.uuid4()) == {"comics": None}


@pytest.mark.parametrize("code,detail,status", [
    ("not_found", {}, 404), ("name_taken", {}, 409), ("built", {}, 409),
    ("image_in_comics", {"comics_id": uuid.uuid4(), "comics_name": "S"}, 409),
    ("image_not_addable", {"reason": "not_active"}, 409), ("image_not_addable", {"reason": "not_found"}, 404),
    ("name_invalid", {"reason": "empty"}, 422), ("reorder_mismatch", {}, 422),
    ("not_buildable", {"reason": "panel_missing"}, 409),
])
def test_http_error_status_and_body(code, detail, status):
    error = http_error(ComicsError(code, **detail))
    assert error.status_code == status and error.detail["code"] == code
    if "comics_id" in detail:
        assert error.detail["comicsId"] == str(detail["comics_id"]) and error.detail["comicsName"] == "S"


def test_panel_shaping_has_kept_and_source_image_id_for_draft_kept_and_retired():
    from Backend.app.services.comics_service import detail
    src = [uuid.uuid4() for _ in range(3)]
    p0 = panel(0, uuid.uuid4())
    p1 = panel(1, uuid.uuid4())
    p2 = panel(2, None)
    for p, s, k in zip((p0, p1, p2), src, (False, True, False)):
        p.source_image_id = s
        p.kept = k
    shaped = detail(comics(state="built", panels=[p0, p1, p2], built_image_id=uuid.uuid4()))["panels"]
    assert [(x["kept"], x["sourceImageId"]) for x in shaped] == [
        (False, str(src[0])), (True, str(src[1])), (False, str(src[2]))]
