"""GET /api/share/{image_id}: a small HTML page with Open Graph / Twitter tags for pasted meme links.

A chat app's preview crawler does not run JavaScript, so the SPA page /memes/{id} shows nothing there. This route
answers with the tags and sends a person on to the real page. Read only. Every dynamic value is HTML-escaped, the
path parameter is validated as a UUID and never echoed, and the file name is never written.

Design: docs/superpowers/specs/2026-11-23-shareable-meme-page-design.md
"""
import html
import json
import uuid
from typing import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.image_store import image_exists
from Backend.app.stage_policy import make_router
from config.settings import settings
from config.stage import is_prod
from Storage.db import get_async_db

router = make_router(prefix="/share", tags=["share"])

DESCRIPTION_LIMIT = 200
DEFAULT_DESCRIPTION = "Open this meme"
TITLE = "A meme"
CACHE_CONTROL = "public, max-age=300"


async def get_share_repository(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[ImageRepository, None]:
    yield ImageRepository(db)


def _description(text: list[str]) -> str:
    """The OCR text joined with spaces, cut by code point to 200 characters (ellipsis included)."""
    joined = " ".join(t.strip() for t in text if t and t.strip())
    if not joined:
        return DEFAULT_DESCRIPTION
    chars = list(joined)
    if len(chars) <= DESCRIPTION_LIMIT:
        return joined
    return "".join(chars[:DESCRIPTION_LIMIT - 1]) + "…"


def _redirect_target(image_id: str) -> str:
    """Relative /memes/{id} on prod and when no frontend origin is configured, else origin + /memes/{id} (dev)."""
    path = f"/memes/{image_id}"
    if is_prod():
        return path
    origin = settings.get("GENERAL.FRONTEND_ORIGIN")
    if origin:
        return str(origin).rstrip("/") + path
    return path


def _script_literal(value: str) -> str:
    """A JSON string literal safe inside a <script> element."""
    return json.dumps(value).replace("</", "<\\/")


def _meta(attr: str, name: str, content: str) -> str:
    return f'<meta {attr}="{name}" content="{html.escape(content, quote=True)}">'


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


@router.get("/{image_id}", response_class=HTMLResponse)
async def get_share_page(
    image_id: str,
    request: Request,
    repo: ImageRepository = Depends(get_share_repository),
):
    try:
        parsed = uuid.UUID(image_id)
    except ValueError:
        raise _not_found()
    canonical_id = str(parsed)

    info = await repo.get_share_info(canonical_id)
    if info is None or info.status != "active" or info.flagged or not image_exists(info.filename):
        raise _not_found()

    base = str(request.base_url).rstrip("/")
    image_url = f"{base}/api/images/{canonical_id}"
    share_url = f"{base}/api/share/{canonical_id}"
    description = _description(info.text)
    target = _redirect_target(canonical_id)

    tags = [
        _meta("property", "og:type", "website"),
        _meta("property", "og:site_name", "Memes"),
        _meta("property", "og:title", TITLE),
        _meta("property", "og:description", description),
        _meta("property", "og:image", image_url),
    ]
    if info.width and info.height:
        tags.append(_meta("property", "og:image:width", str(info.width)))
        tags.append(_meta("property", "og:image:height", str(info.height)))
    tags += [
        _meta("property", "og:url", share_url),
        _meta("name", "twitter:card", "summary_large_image"),
        _meta("name", "twitter:title", TITLE),
        _meta("name", "twitter:description", description),
        _meta("name", "twitter:image", image_url),
    ]
    escaped_target = html.escape(target, quote=True)
    page = (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{TITLE}</title>\n"
        + "\n".join(tags) + "\n"
        f'<meta http-equiv="refresh" content="0; url={escaped_target}">\n'
        f"<script>location.replace({_script_literal(target)});</script>\n"
        "</head>\n<body>\n"
        f'<p><a href="{escaped_target}">{DEFAULT_DESCRIPTION}</a></p>\n'
        "</body>\n</html>\n"
    )
    return HTMLResponse(page, headers={"Cache-Control": CACHE_CONTROL})
