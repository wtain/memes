"""Image file containment (P5a-T5, decision 19): a file name must name a plain file directly inside the images
directory. The same hostile names are sent through the full prod app in test_prod_stage.py."""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import Backend.app.main as main
from Backend.app.services import image_store
from Backend.app.services.image_store import UnsafeImagePath, get_image_path, image_exists
from Storage.db import get_async_db


def _symlink(link, target, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create symbolic links")


@pytest.fixture
def store(tmp_path, monkeypatch):
    images = tmp_path / "images"
    images.mkdir()
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    (images / "ok.jpg").write_bytes(b"OKBYTES")
    (images / "adir").mkdir()
    (images / "sub").mkdir()
    (images / "sub" / "inner.jpg").write_bytes(b"INNER")
    monkeypatch.setattr(image_store, "_IMAGES_DIR", images)
    monkeypatch.setattr(image_store, "_REJECTED_DIR", images / "rejected")
    monkeypatch.setattr(image_store, "_BUG_REPORTS_DIR", tmp_path / "bug-reports")
    return images


HOSTILE = [
    "../secret.txt", "..", ".", "", "%2e%2e/secret.txt", "/etc/passwd", "C:\\Windows\\win.ini", "a\x00b.jpg",
    "sub/inner.jpg", "sub\\inner.jpg", "adir/../ok.jpg", "ok.jpg/../../secret.txt", "adir",
]


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_names_are_refused(store, name):
    with pytest.raises(UnsafeImagePath):
        get_image_path(name)
    assert image_exists(name) is False


def test_a_plain_file_is_served(store):
    assert get_image_path("ok.jpg") == store / "ok.jpg"
    assert image_exists("ok.jpg") is True


def test_names_that_merely_look_encoded_are_plain_names(store):
    """The name comes from the database, not from the URL: no decoding happens here, and a literal '%2e%2e' is
    just an unusual file name that does not exist."""
    assert get_image_path("%2e%2e") == store / "%2e%2e"
    assert image_exists("%2e%2e") is False
    assert image_exists("..%2fsecret.txt") is False


def test_a_missing_file_has_a_path_but_does_not_exist(store):
    assert image_exists("missing.jpg") is False


def test_a_directory_is_not_an_image(store):
    assert image_exists("adir") is False


def test_symlink_to_a_file_outside_is_refused(store):
    _symlink(store / "linkout.jpg", store.parent / "secret.txt")
    with pytest.raises(UnsafeImagePath):
        get_image_path("linkout.jpg")
    assert image_exists("linkout.jpg") is False


def test_symlink_to_a_file_inside_is_refused_too(store):
    _symlink(store / "linkin.jpg", store / "ok.jpg")
    with pytest.raises(UnsafeImagePath):
        get_image_path("linkin.jpg")
    assert image_exists("linkin.jpg") is False


def test_a_name_through_a_symlinked_directory_is_refused(store):
    _symlink(store / "linkdir", store.parent, directory=True)
    for name in ("linkdir", "linkdir/secret.txt", "linkdir/../secret.txt"):
        assert image_exists(name) is False


def test_a_symlinked_images_directory_itself_is_fine(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    (real / "ok.jpg").write_bytes(b"x")
    link = tmp_path / "link"
    _symlink(link, real, directory=True)
    monkeypatch.setattr(image_store, "_IMAGES_DIR", link)
    assert image_exists("ok.jpg") is True


def test_move_helpers_and_bug_reports_apply_the_same_rule(store):
    for name in ("../secret.txt", "sub/inner.jpg"):
        with pytest.raises(UnsafeImagePath):
            image_store.move_to_rejected(name)
        with pytest.raises(UnsafeImagePath):
            image_store.move_from_rejected(name)
        with pytest.raises(UnsafeImagePath):
            image_store.save_bug_report(name, b"x")
    image_store.move_to_rejected("ok.jpg")
    assert not (store / "ok.jpg").exists() and (store / "rejected" / "ok.jpg").exists()
    image_store.move_from_rejected("ok.jpg")
    assert (store / "ok.jpg").exists()
    image_store.save_bug_report("r.txt", b"report")
    assert (store.parent / "bug-reports" / "r.txt").read_bytes() == b"report"


class _FakeSession:
    pass


class _Repo:
    def __init__(self, _db):
        pass

    async def get_filename(self, image_id):
        return {"h1": "../secret.txt", "h2": "sub/inner.jpg", "h3": "ok.jpg"}.get(image_id, image_id)


def test_the_file_route_answers_404_for_a_refused_name_and_200_for_a_plain_file(store):
    """Dev app, as it runs today: the containment applies on every stage."""
    main.app.dependency_overrides[get_async_db] = lambda: _FakeSession()
    try:
        with patch("Backend.app.api.images.ImageRepository", _Repo):
            client = TestClient(main.app)
            for image_id in ("h1", "h2", "..%2fsecret.txt", "%2e%2e"):
                response = client.get(f"/api/images/{image_id}")
                assert response.status_code == 404, image_id
                assert "TOPSECRET" not in response.text and "INNER" not in response.text
            ok = client.get("/api/images/h3")
            assert ok.status_code == 200 and ok.content == b"OKBYTES"
    finally:
        main.app.dependency_overrides.clear()
