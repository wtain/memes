import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.meme_profile_service import (
    MemeProfileService, ProfileConflictError, ProfileValidationError, UnknownImageError,
)
from Backend.app.types.generated.curatedprofile import Schema as CuratedProfile
from Backend.app.types.generated.curatedprofilesaverequest import Schema as CuratedProfileSaveRequest
from config.settings import settings
from parse.config import resolve_ollama_url
from repository.meme_profiles import MemeProfileRepository
from retrieval.embed import OllamaEmbedClient
from Storage.db import get_async_db
from Storage.models import PROFILE_EMBEDDING_DIM

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meme-from-text", tags=["meme-from-text"])


async def get_profile_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[MemeProfileService, None]:
    embedder = OllamaEmbedClient(resolve_ollama_url(str(settings.get("parse.ollama_url"))), PROFILE_EMBEDDING_DIM)
    yield MemeProfileService(
        MemeProfileRepository(db), embedder,
        embedding_model=str(settings.get("taxonomy.embedding_model")),
        embed_timeout=float(settings.get("profile_editor.embed_timeout_seconds", 8.0)),
    )


def _fail(error: Exception) -> HTTPException:
    """Map an error without ever echoing the profile text: fixed details only; unexpected ones log the type."""
    if isinstance(error, ProfileValidationError):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, UnknownImageError):
        return HTTPException(status_code=404, detail="image not found")
    if isinstance(error, ProfileConflictError):
        return HTTPException(status_code=409, detail=str(error))
    logger.warning("profile request failed: %s", type(error).__name__)
    return HTTPException(status_code=500, detail="profile request failed")


@router.get("/profiles/{image_id}", response_model=CuratedProfile)
async def get_profile(image_id: str, service: MemeProfileService = Depends(get_profile_service)):
    try:
        return await service.get(image_id)
    except Exception as error:
        raise _fail(error) from None


@router.put("/profiles/{image_id}", response_model=CuratedProfile)
async def save_profile(image_id: str, request: CuratedProfileSaveRequest,
                       service: MemeProfileService = Depends(get_profile_service)):
    try:
        return await service.save(image_id, request)
    except Exception as error:
        raise _fail(error) from None


@router.post("/profiles/{image_id}/confirm", response_model=CuratedProfile)
async def confirm_profile(image_id: str, service: MemeProfileService = Depends(get_profile_service)):
    try:
        return await service.confirm(image_id)
    except Exception as error:
        raise _fail(error) from None


@router.delete("/profiles/{image_id}", status_code=204)
async def reset_profile(image_id: str, service: MemeProfileService = Depends(get_profile_service)):
    try:
        await service.reset(image_id)
    except Exception as error:
        raise _fail(error) from None
    return Response(status_code=204)
