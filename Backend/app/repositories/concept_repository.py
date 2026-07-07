from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import Concept, Embedding, Image, ConceptImageSet, ConceptImage
from repository.concepts import ConceptRepositoryBase


class ConceptRepository(ConceptRepositoryBase):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, concept_id: int):
        result = await self.session.execute(
            select(Concept.id, Concept.name).where(Concept.id == concept_id)
        )
        return result.one()

    async def get_embedding(self, concept_id: int):
        result = await self.session.execute(
            select(Concept.embedding).where(Concept.id == concept_id)
        )
        return result.scalar_one()

    # deprecate
    async def get_top_images(self, concept_embedding, limit: int = 10):
        result = await self.session.execute(
            select(Embedding.image_id, Embedding.embedding.cosine_distance(concept_embedding), Image.filename)
            .join(Image, Image.id == Embedding.image_id)
            .order_by(Embedding.embedding.cosine_distance(concept_embedding))
            .limit(limit)
        )
        return result.all()

    async def get_image_embedding(self, image_id: str):
        result = await self.session.execute(
            select(Embedding.embedding).filter(Embedding.image_id == image_id)
        )
        return result.scalars().first()

    # deprecate
    async def get_for_image(self, image_embedding, limit: int = 10):
        result = await self.session.execute(
            select(
                ConceptImage.id.label("image_id"),
                Concept.id,
                Concept.name,
                ConceptImageSet.id.label("cid"),
                ConceptImageSet.name.label("cname"),
            )
            .join(ConceptImage, ConceptImage.concept_image_set_id == ConceptImageSet.id)
            .join(Concept, Concept.id == ConceptImageSet.concept_id)
            .order_by(
                ConceptImage.embedding.cosine_distance(image_embedding)
            )
            .limit(limit)
        )
        return result.all()

    async def top_concepts_for_image(self, image_id: str, threshold: float = 0.2):
        ci = ConceptImage
        cs = ConceptImageSet
        c = Concept

        embedding = await self.get_image_embedding(image_id)
        if embedding is None:
            return None
        distance = ci.embedding.cosine_distance(embedding)

        subquery = (
            select(
                c.name.label("name"),
                c.id.label("id"),
                distance.label("distance")
            )
            .select_from(ci)
            .join(cs, ci.concept_image_set_id == cs.id)
            .join(c, cs.concept_id == c.id)
            .where(distance < threshold)
            .subquery()
        )

        query = (
            select(
                subquery.c.name,
                subquery.c.id,
                func.avg(subquery.c.distance).label("avg_distance")
            )
            .group_by(subquery.c.name, subquery.c.id)
            .order_by(func.avg(subquery.c.distance))
        )
        result = await self.session.execute(query)
        return result.all()