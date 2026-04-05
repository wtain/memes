from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Response

from app.models.external import AsyncSessionLocal, get_async_db
from app.repositories.concept_repository import ConceptRepository
from app.services.concept_service import ConceptService
from app.types.generated.concept import Schema as ConceptDto
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse

router = APIRouter(prefix="/concepts", tags=["concepts"])


async def get_concept_service(db: AsyncSessionLocal = Depends(get_async_db)) -> AsyncGenerator[ConceptService, None]:
    service = ConceptService(ConceptRepository(db))
    try:
        yield service
    finally:
        # Optionally do cleanup if needed
        pass
        # The session will be closed by get_async_db's finally block


@router.get("", response_model=list[ConceptDto])
async def get_concepts(response: Response, service: ConceptService = Depends(get_concept_service)):
    return await service.get_all()


@router.get("/top-images", response_model=MemeSearchResponse)
async def get_top_images_for_concept(
    response: Response,
    concept_id: int,
    service: ConceptService = Depends(get_concept_service),
):
    return await service.get_top_images(concept_id)


@router.get("/for-image", response_model=list[ConceptDto])
async def get_concepts_for_image(
    response: Response,
    image_id: str,
    service: ConceptService = Depends(get_concept_service),
):
    return await service.get_for_image(image_id)


@router.get("/{concept_id}", response_model=ConceptDto)
async def get_concept(
    response: Response,
    concept_id: int,
    service: ConceptService = Depends(get_concept_service),
):
    return await service.get_by_id(concept_id)
