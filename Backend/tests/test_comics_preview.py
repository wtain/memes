import io
import types
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image as PILImage

from Backend.app.api.comics import get_comics_service, router
from Backend.app.services.comics_service import ComicsService
from batch.utils.comics_compose import compose_preview, plan_layout
from Storage.models import ComicsState


def _panel(position, image_id):
    return types.SimpleNamespace(position=position, image_id=image_id)


class FakeRepo:
    def __init__(self, comics):
        self.comics = comics

    async def get(self, comics_id, lock=False):
        return self.comics


def _make(tmp_path, names, state="draft", write=True, null_at=None):
    ids = [uuid.uuid4() for _ in names]
    files = dict(zip(ids, names))
    if write:
        for name in names:
            PILImage.new("RGB", (300, 200), "red").save(tmp_path / name)
    panels = [_panel(i, None if i == null_at else image_id) for i, image_id in enumerate(ids)]
    comics = types.SimpleNamespace(id=uuid.uuid4(), state=state, panels=list(reversed(panels)))
    service = ComicsService(None, base_path=str(tmp_path))
    service.repo = FakeRepo(comics)

    async def filenames(image_ids):
        return {i: files[i] for i in image_ids if i in files}

    service._filenames = filenames
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_comics_service] = lambda: service
    return TestClient(app), comics


def test_preview_returns_jpeg_bytes_of_the_tiled_panels(tmp_path):
    client, comics = _make(tmp_path, ["a.png", "b.png", "c.png"])
    response = client.get(f"/api/comics/{comics.id}/preview?maxWidth=400")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    layout = plan_layout([(300, 200)] * 3)
    scale = min(1.0, 400 / layout.canvas_width)
    assert PILImage.open(io.BytesIO(response.content)).size == (
        round(layout.canvas_width * scale), round(layout.canvas_height * scale))
    paths = [str(tmp_path / n) for n in ("a.png", "b.png", "c.png")]
    assert response.content == compose_preview(paths, 400)


def test_preview_of_an_empty_draft_is_422_no_panels(tmp_path):
    client, comics = _make(tmp_path, [])
    response = client.get(f"/api/comics/{comics.id}/preview")
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": "compose_failed", "reason": "no_panels", "position": None}


def test_preview_with_a_corrupt_panel_names_its_one_based_position(tmp_path):
    client, comics = _make(tmp_path, ["a.png", "b.png"])
    (tmp_path / "b.png").write_bytes(b"not an image")
    detail = client.get(f"/api/comics/{comics.id}/preview").json()["detail"]
    assert (detail["code"], detail["reason"], detail["position"]) == ("compose_failed", "unreadable", 2)


def test_preview_with_a_missing_file_is_unreadable_not_500(tmp_path):
    client, comics = _make(tmp_path, ["a.png", "b.png"])
    (tmp_path / "a.png").unlink()
    response = client.get(f"/api/comics/{comics.id}/preview")
    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "unreadable" and response.json()["detail"]["position"] == 1


def test_preview_of_a_built_comics_is_409(tmp_path):
    client, comics = _make(tmp_path, ["a.png"], state=ComicsState.BUILT)
    response = client.get(f"/api/comics/{comics.id}/preview")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "built"


def test_a_panel_without_an_image_is_a_clear_422(tmp_path):
    client, comics = _make(tmp_path, ["a.png", "b.png"], null_at=1)
    detail = client.get(f"/api/comics/{comics.id}/preview").json()["detail"]
    assert (detail["reason"], detail["position"]) == ("unreadable", 2)


@pytest.mark.parametrize("width", [199, 2401])
def test_max_width_bounds_are_422(tmp_path, width):
    client, comics = _make(tmp_path, ["a.png"])
    assert client.get(f"/api/comics/{comics.id}/preview?maxWidth={width}").status_code == 422


def test_max_width_bounds_are_accepted(tmp_path):
    client, comics = _make(tmp_path, ["a.png"])
    for width in (200, 2400):
        assert client.get(f"/api/comics/{comics.id}/preview?maxWidth={width}").status_code == 200


def test_semaphore_is_released_after_a_failure(tmp_path):
    import Backend.app.services.comics_service as module
    bad, comics_bad = _make(tmp_path, ["a.png"], write=False)
    good, comics_good = _make(tmp_path, ["g.png"])
    for _ in range(3):  # more failures than the semaphore has slots
        assert bad.get(f"/api/comics/{comics_bad.id}/preview").status_code == 422
    assert good.get(f"/api/comics/{comics_good.id}/preview").status_code == 200
    assert module._PREVIEW_SEMAPHORE._value == 2


def test_cache_control_is_no_store(tmp_path):
    client, comics = _make(tmp_path, ["a.png"])
    assert client.get(f"/api/comics/{comics.id}/preview").headers["cache-control"] == "no-store"
