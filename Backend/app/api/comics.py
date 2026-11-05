import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.comics_service import MAX_ADDABILITY_IDS, ComicsService, http_error
from Backend.app.types.generated.comicsaddability import Schema as ComicsAddabilitySchema
from Backend.app.types.generated.comicsaddpanelrequest import Schema as AddPanelRequest
from Backend.app.types.generated.comicsbyimage import Schema as ComicsByImageSchema
from Backend.app.types.generated.comicscreaterequest import Schema as ComicsCreateRequest
from Backend.app.types.generated.comicsdetail import Schema as ComicsDetailSchema
from Backend.app.types.generated.comicspage import Schema as ComicsPageSchema
from Backend.app.types.generated.comicsreorderrequest import Schema as ReorderRequest
from repository.comics import ComicsError
from Storage.db import get_async_db

router = APIRouter(prefix="/comics", tags=["comics"])


async def get_comics_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[ComicsService, None]:
    yield ComicsService(db)


async def _run(call, *args):
    # Raising after the service wrote anything is safe: get_async_db rolls the whole request back.
    try:
        return await call(*args)
    except ComicsError as error:
        raise http_error(error) from None


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


@router.get("/by-image/{image_id}", response_model=ComicsByImageSchema)
async def get_by_image(image_id: uuid.UUID, service: ComicsService = Depends(get_comics_service)):
    return await _run(service.by_image, image_id)


@router.get("/{comics_id}", response_model=ComicsDetailSchema)
async def get_comics(comics_id: uuid.UUID, service: ComicsService = Depends(get_comics_service)):
    return await _run(service.detail, comics_id)


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
