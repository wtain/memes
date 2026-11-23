"""Tests for GET /api/share/{image_id}: the link-preview page for pasted meme links."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api import share as share_module
from Backend.app.api.share import router as share_router, get_share_repository
from Backend.app.repositories.image_repository import ShareInfo

app = FastAPI()
app.include_router(share_router, prefix="/api")

IMAGE_ID = str(uuid.uuid4())


def info(**overrides) -> ShareInfo:
    values = dict(filename="secret-client-name.jpg", status="active", flagged=False,
                  width=800, height=600, text=["hello", "world"])
    values.update(overrides)
    return ShareInfo(**values)


@pytest.fixture
def repo():
    repo = AsyncMock()
    repo.get_share_info.return_value = info()
    return repo


@pytest.fixture
def client(repo):
    async def override():
        yield repo

    app.dependency_overrides[get_share_repository] = override
    with patch.object(share_module, "image_exists", return_value=True), \
            patch.object(share_module, "is_prod", return_value=False), \
            patch.object(share_module, "settings", MagicMock(get=MagicMock(return_value=None))):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def test_share_page_has_preview_tags(client):
    r = client.get(f"/api/share/{IMAGE_ID}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "public, max-age=300"
    body = r.text
    assert '<meta property="og:title" content="A meme">' in body
    assert '<meta property="og:description" content="hello world">' in body
    assert f'<meta property="og:image" content="http://testserver/api/images/{IMAGE_ID}">' in body
    assert '<meta property="og:image:width" content="800">' in body
    assert '<meta property="og:image:height" content="600">' in body
    assert '<meta name="twitter:card" content="summary_large_image">' in body
    assert f'<meta property="og:url" content="http://testserver/api/share/{IMAGE_ID}">' in body


def test_description_falls_back_when_no_text(client, repo):
    repo.get_share_info.return_value = info(text=[])
    assert 'property="og:description" content="Open this meme"' in client.get(f"/api/share/{IMAGE_ID}").text


def test_description_is_cut_at_200_chars_with_ellipsis(client, repo):
    repo.get_share_info.return_value = info(text=["a" * 500])
    body = client.get(f"/api/share/{IMAGE_ID}").text
    assert 'property="og:description" content="' + "a" * 199 + "…" + '"' in body
    assert share_module._description(["a" * 500]) == "a" * 199 + "…"
    assert len(share_module._description(["a" * 500])) == 200


def test_description_cut_does_not_split_code_points(client, repo):
    repo.get_share_info.return_value = info(text=["😀" * 300])
    r = client.get(f"/api/share/{IMAGE_ID}")
    body = r.content.decode("utf-8")  # raises on a lone surrogate
    assert "😀" * 199 + "…" in body
    assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in body)


def test_html_is_escaped(client, repo):
    repo.get_share_info.return_value = info(text=["<script>alert(1)</script> \"q\" & 'x'"])
    body = client.get(f"/api/share/{IMAGE_ID}").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &quot;q&quot; &amp; &#x27;x&#x27;" in body
    assert 'property="og:description" content="&lt;script&gt;' in body


def test_file_name_never_in_body(client):
    assert "secret-client-name" not in client.get(f"/api/share/{IMAGE_ID}").text


def test_size_omitted_when_unknown(client, repo):
    repo.get_share_info.return_value = info(width=None, height=None)
    body = client.get(f"/api/share/{IMAGE_ID}").text
    assert "og:image:width" not in body
    assert "og:image:height" not in body


def test_size_omitted_when_only_one_known(client, repo):
    repo.get_share_info.return_value = info(width=800, height=None)
    body = client.get(f"/api/share/{IMAGE_ID}").text
    assert "og:image:width" not in body


@pytest.mark.parametrize("case", [
    "bad-uuid", "traversal", "unknown", "pending", "rejected", "cut_proposed", "flagged", "file_missing",
])
def test_404_cases(client, repo, case):
    path_id = IMAGE_ID
    exists = True
    if case == "bad-uuid":
        path_id = "not-a-uuid"
    elif case == "traversal":
        path_id = "..%2F.."
    elif case == "unknown":
        repo.get_share_info.return_value = None
    elif case in ("pending", "rejected", "cut_proposed"):
        repo.get_share_info.return_value = info(status=case)
    elif case == "flagged":
        repo.get_share_info.return_value = info(flagged=True)
    elif case == "file_missing":
        exists = False
    with patch.object(share_module, "image_exists", return_value=exists):
        r = client.get(f"/api/share/{path_id}")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    assert "not-a-uuid" not in r.text
    assert "secret-client-name" not in r.text


def test_prod_redirect_is_relative(client):
    with patch.object(share_module, "is_prod", return_value=True), \
            patch.object(share_module, "settings", MagicMock(get=MagicMock(return_value="http://localhost:5174"))):
        body = client.get(f"/api/share/{IMAGE_ID}").text
    assert f'content="0; url=/memes/{IMAGE_ID}"' in body
    assert f'location.replace("/memes/{IMAGE_ID}")' in body
    assert f'<a href="/memes/{IMAGE_ID}">Open this meme</a>' in body
    assert "localhost:5174" not in body


def test_dev_redirect_uses_frontend_origin(client):
    with patch.object(share_module, "settings", MagicMock(get=MagicMock(return_value="http://localhost:5174"))):
        body = client.get(f"/api/share/{IMAGE_ID}").text
    assert f'content="0; url=http://localhost:5174/memes/{IMAGE_ID}"' in body
    assert f'<a href="http://localhost:5174/memes/{IMAGE_ID}">' in body


def test_dev_redirect_relative_when_origin_unset(client):
    body = client.get(f"/api/share/{IMAGE_ID}").text
    assert f'content="0; url=/memes/{IMAGE_ID}"' in body


def test_script_target_neutralises_closing_tag():
    assert "</" not in share_module._script_literal("http://x/</script>")


def test_public_host_used_behind_proxy(repo):
    async def override():
        yield repo

    app.dependency_overrides[get_share_repository] = override
    try:
        with patch.object(share_module, "image_exists", return_value=True), \
                patch.object(share_module, "is_prod", return_value=False), \
                patch.object(share_module, "settings", MagicMock(get=MagicMock(return_value=None))):
            with TestClient(app, base_url="https://memes.example") as c:
                body = c.get(f"/api/share/{IMAGE_ID}").text
    finally:
        app.dependency_overrides.clear()
    assert f'property="og:image" content="https://memes.example/api/images/{IMAGE_ID}"' in body
    assert f'property="og:url" content="https://memes.example/api/share/{IMAGE_ID}"' in body
