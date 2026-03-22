from fastapi import APIRouter
from fastapi import Response
from fastapi.params import Depends
from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.models.external import Concept, AsyncSessionLocal, get_async_db, Embedding, Image
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse
from app.types.generated.meme import Schema as Meme

from app.types.generated.concept import Schema as ConceptDto

router = APIRouter(prefix="/concepts", tags=["concepts"])

@router.get("")
async def get_concepts(
    response: Response,
    db: AsyncSessionLocal = Depends(get_async_db)
):
    query = (
        select(
            Concept.id,
            Concept.name
        )
    )
    result = await db.execute(query)
    results = result.all()

    return [ConceptDto(id=id, name=name) for (id, name, ) in results]


@router.get("/top-images", response_model=MemeSearchResponse)
async def get_top_images_for_concept(
    response: Response,
    concept_id: int,
    db: AsyncSessionLocal = Depends(get_async_db)
):
    img = aliased(Image)
    embed = aliased(Embedding)
    concept = aliased(Concept)

    concept_query = (
        select(
            concept.embedding
        )
        .where(concept.id == concept_id)
    )
    result_concept = await db.execute(concept_query)
    concept_embedding = result_concept.scalar_one()

    query = (
        select(
            embed.image_id,
            embed.embedding.cosine_distance(concept_embedding)
        )
        .order_by(
            embed.embedding.cosine_distance(concept_embedding)
        ).limit(10)
    )
    result = await db.execute(query)
    results = result.all()

    items = [Meme(id=str(image_id), imageUrl=f"/api/images/{str(image_id)}", text=[], tags=[], ) for
             (image_id, similarity,) in results]

    response_memes = MemeSearchResponse(items=items)

    return response_memes


@router.get("/for-image")
async def get_concepts_for_image(
    response: Response,
    image_id: str,
    db: AsyncSessionLocal = Depends(get_async_db)
):
    query_image = (
        select(
            Embedding.embedding
        )
        .filter(
            Embedding.image_id == image_id
        )
    )
    result_image = await db.execute(query_image)
    image_embedding = result_image.scalar_one()

    query = (
        select(
            Concept.id,
            Concept.name,
            # Concept.embedding.cosine_distance(image_embedding)
        )
        .order_by(
            Concept.embedding.cosine_distance(image_embedding)
        ).limit(10)
    )
    result = await db.execute(query)
    results = result.all()

    return [ConceptDto(id=id, name=name) for (id, name, ) in results]


@router.get("/{concept_id}")
async def get_concept(
    response: Response,
    concept_id: int,
    db: AsyncSessionLocal = Depends(get_async_db)
):
    query = (
        select(
            Concept.id,
            Concept.name,
        )
        .where(
            Concept.id == concept_id
        )
    )
    result = await db.execute(query)
    results = result.all()
    (id, name, ) = results[0]

    return ConceptDto(id=id, name=name)
