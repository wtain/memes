import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.cache import no_cache_headers
from Backend.app.services.meme_from_text_pipeline import get_pipeline
from Backend.app.services.meme_from_text_service import MemeFromTextService
from Backend.app.types.generated.memefromtextrequest import Schema as MemeFromTextRequest
from Backend.app.types.generated.memefromtextresponse import Schema as MemeFromTextResponse
from repository.meme_retrieval import MemeRetrievalRepository
from retrieval.pipeline import InvalidProfile
from Storage.db import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meme-from-text", tags=["meme-from-text"])


async def get_meme_from_text_service(
    db: AsyncSession = Depends(get_async_db),
) -> AsyncGenerator[MemeFromTextService, None]:
    yield MemeFromTextService(get_pipeline(), MemeRetrievalRepository(db), ImageRepository(db))


@router.post("/search", response_model=MemeFromTextResponse, response_model_exclude_none=True)
async def search_memes_from_text(
    request: MemeFromTextRequest,
    response: Response,
    service: MemeFromTextService = Depends(get_meme_from_text_service),
):
    response.headers.update(no_cache_headers())
    try:
        return await service.search(request)
    except InvalidProfile as error:
        raise HTTPException(status_code=422, detail=error.errors) from None
    except Exception as error:
        # A database error on the lexical path carries the user's lemmas as statement parameters, and uvicorn logs
        # an escaping exception with them: log the type name only and raise a generic error without a chain.
        logger.warning("meme-from-text search failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="meme-from-text search failed") from None
