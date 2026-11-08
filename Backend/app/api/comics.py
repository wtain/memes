import logging
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.comics_build_service import ComicsBuildService
from Backend.app.services.comics_candidates_service import ComicsCandidatesService, candidate_http_error
from Backend.app.services.comics_pipeline_trigger import start_comics_pipeline
from Backend.app.services.comics_service import (MAX_ADDABILITY_IDS, ComicsService, detail, compose_http_error,
                                                  http_error)
from Backend.app.types.generated.comicsbuildrequest import Schema as ComicsBuildRequest
from Backend.app.types.generated.comicsbuildresult import Schema as ComicsBuildResultSchema
from Backend.app.types.generated.comicsaddability import Schema as ComicsAddabilitySchema
from Backend.app.types.generated.comicsaddpanelrequest import Schema as AddPanelRequest
from Backend.app.types.generated.comicsbyimage import Schema as ComicsByImageSchema
from Backend.app.types.generated.comicscandidateacceptrequest import Schema as ComicsCandidateAcceptRequest
from Backend.app.types.generated.comicscandidatepage import Schema as ComicsCandidatePageSchema
from Backend.app.types.generated.comicscreaterequest import Schema as ComicsCreateRequest
from Backend.app.types.generated.comicsdetail import Schema as ComicsDetailSchema
from Backend.app.types.generated.comicspage import Schema as ComicsPageSchema
from Backend.app.types.generated.comicsreorderrequest import Schema as ReorderRequest
from batch.utils.comics_compose import ComposeError
from repository.comics import ComicsError
from repository.comics_candidates import CandidateError
from Storage.db import get_async_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/comics", tags=["comics"])


async def get_comics_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[ComicsService, None]:
    yield ComicsService(db)


async def get_comics_build_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[ComicsBuildService, None]:
    yield ComicsBuildService(db)


async def get_candidates_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[ComicsCandidatesService, None]:
    yield ComicsCandidatesService(db)


async def _run(call, *args):
    # Raising after the service wrote anything is safe: get_async_db rolls the whole request back.
    try:
        return await call(*args)
    except ComicsError as error:
        raise http_error(error) from None
    except ComposeError as error:
        raise compose_http_error(error) from None
    except CandidateError as error:
        raise candidate_http_error(error) from None


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid id") from None


@router.get("", response_model=ComicsPageSchema)
async def list_comics(q: str | None = Query(None), limit: int = Query(50, ge=1, le=100),
                      offset: int = Query(0, ge=0), service: ComicsService = Depends(get_comics_service)):
    return await _run(service.list, q, limit, offset)


@router.post("", response_model=ComicsDetailSchema, status_code=201)
async def create_comics(body: ComicsCreateRequest, service: ComicsService = Depends(get_comics_service)):
    return await _run(service.create, body.name)


# Static paths come before /{comics_id}.
@router.get("/addability", response_model=ComicsAddabilitySchema)
async def get_addability(imageIds: str = Query(...), service: ComicsService = Depends(get_comics_service)):
    parts = imageIds.split(",")
    if len(parts) > MAX_ADDABILITY_IDS:
        raise HTTPException(status_code=422, detail=f"imageIds takes 1 to {MAX_ADDABILITY_IDS} ids")
    return await _run(service.addability, [_uuid(p) for p in parts])


@router.get("/candidates", response_model=ComicsCandidatePageSchema)
async def list_candidates(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                          service: ComicsCandidatesService = Depends(get_candidates_service)):
    return await _run(service.list, offset, limit)


@router.post("/candidates/{candidate_id}/accept", response_model=ComicsDetailSchema, status_code=201)
async def accept_candidate(candidate_id: uuid.UUID, body: ComicsCandidateAcceptRequest | None = None,
                           service: ComicsCandidatesService = Depends(get_candidates_service)):
    ids = [_uuid(p) for p in body.imageIds] if body is not None and body.imageIds is not None else None
    return await _run(service.accept, candidate_id, ids)


@router.post("/candidates/{candidate_id}/dismiss", status_code=204)
async def dismiss_candidate(candidate_id: uuid.UUID,
                            service: ComicsCandidatesService = Depends(get_candidates_service)):
    await _run(service.dismiss, candidate_id)
    return Response(status_code=204)


@router.get("/by-image/{image_id}", response_model=ComicsByImageSchema)
async def get_by_image(image_id: uuid.UUID, service: ComicsService = Depends(get_comics_service)):
    return await _run(service.by_image, image_id)


@router.get("/{comics_id}", response_model=ComicsDetailSchema)
async def get_comics(comics_id: uuid.UUID, service: ComicsService = Depends(get_comics_service)):
    return await _run(service.detail, comics_id)


@router.get("/{comics_id}/preview", response_class=Response, responses={200: {"content": {"image/jpeg": {}}}})
async def preview_comics(comics_id: uuid.UUID, maxWidth: int = Query(1200, ge=200, le=2400),
                         service: ComicsService = Depends(get_comics_service)):
    data = await _run(service.preview, comics_id, maxWidth)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/{comics_id}/build", response_model=ComicsBuildResultSchema)
async def build_comics(comics_id: uuid.UUID, body: ComicsBuildRequest | None = None,
                       service: ComicsBuildService = Depends(get_comics_build_service),
                       db: AsyncSession = Depends(get_async_db)):
    keep = [_uuid(p) for p in (body.keepPanelIds if body else [])]
    comics, built_image_id = await _run(service.build, comics_id, keep)
    result = {"comics": detail(comics), "builtImageId": str(built_image_id)}
    # The same session as the service's (FastAPI caches get_async_db per request). Committed here, not left to
    # get_async_db: that dependency exits after the response is sent (and after any background task), but the
    # pipeline's subprocess must find the image row. Like AdminBatchService.trigger_run's own early commit.
    await db.commit()
    try:
        await start_comics_pipeline()  # best effort, never raises; the build stands either way
    except Exception:
        logger.warning("comics: starting comics_pipeline after build failed", exc_info=True)
    return result


@router.patch("/{comics_id}", response_model=ComicsDetailSchema)
async def rename_comics(comics_id: uuid.UUID, body: ComicsCreateRequest,
                        service: ComicsService = Depends(get_comics_service)):
    return await _run(service.rename, comics_id, body.name)


@router.delete("/{comics_id}", status_code=204)
async def delete_comics(comics_id: uuid.UUID, service: ComicsService = Depends(get_comics_service)):
    await _run(service.delete, comics_id)
    return Response(status_code=204)


@router.post("/{comics_id}/panels", response_model=ComicsDetailSchema, status_code=201)
async def add_panel(comics_id: uuid.UUID, body: AddPanelRequest, service: ComicsService = Depends(get_comics_service)):
    return await _run(service.add_panel, comics_id, _uuid(body.imageId), bool(body.move))


@router.put("/{comics_id}/panels/order", response_model=ComicsDetailSchema)
async def reorder_panels(comics_id: uuid.UUID, body: ReorderRequest,
                         service: ComicsService = Depends(get_comics_service)):
    return await _run(service.reorder, comics_id, [_uuid(p) for p in body.panelIds])


@router.delete("/{comics_id}/panels/{panel_id}", status_code=204)
async def remove_panel(comics_id: uuid.UUID, panel_id: uuid.UUID,
                       service: ComicsService = Depends(get_comics_service)):
    await _run(service.remove_panel, comics_id, panel_id)
    return Response(status_code=204)
