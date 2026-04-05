from pgvector import Vector
from sqlalchemy import select, func, bindparam, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.external import Concept, Embedding, Image, ConceptImageSet, ConceptImage


class ConceptRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self):
        result = await self.session.execute(select(Concept.id, Concept.name))
        return result.all()

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

    async def top_images_for_concept(
            self,
            concept_id: int,
            threshold: float = 0.2,
            limit: int = 50
    ):
        ci = ConceptImage
        cis = ConceptImageSet
        i = Image
        e = Embedding

        # --- concept embeddings ---
        concept_embeddings_subq = (
            select(ci.embedding.label("embedding"))
            .join(cis, ci.concept_image_set_id == cis.id)
            .where(cis.concept_id == concept_id)
            .subquery()
        )

        ce = concept_embeddings_subq.c.embedding

        # --- image embeddings ---
        image_embeddings_subq = (
            select(
                i.id.label("image_id"),
                i.filename.label("filename"),
                e.embedding.label("embedding"),
            )
            .join(e, e.image_id == i.id)
            .subquery()
        )

        ie = image_embeddings_subq.c

        # --- distance expression ---
        distance = ie.embedding.cosine_distance(ce)

        # --- distances subquery ---
        distances_subq = (
            select(
                ie.image_id,
                ie.filename,
                distance.label("distance")
            )
            .select_from(image_embeddings_subq)
            .join(concept_embeddings_subq, true())
            .subquery()
        )

        d = distances_subq.c

        # --- final aggregation ---
        match_count = func.count().label("match_count")
        avg_distance = func.avg(d.distance).label("avg_distance")
        score = func.sum(func.exp(-d.distance)).label("score")

        query = (
            select(
                d.image_id,
                d.filename,
                match_count,
                avg_distance,
                score
            )
            .where(d.distance < threshold)
            .group_by(d.image_id, d.filename)
            .order_by(score.desc())
            .limit(limit)
        )

        result = await self.session.execute(query)
        return result.all()

    async def get_image_embedding(self, image_id: str):
        result = await self.session.execute(
            select(Embedding.embedding).filter(Embedding.image_id == image_id)
        )
        return result.scalar_one()

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
                # Concept.embedding.cosine_distance(image_embedding)
                # ConceptImageSet.centroid_embedding.cosine_distance(image_embedding)
                ConceptImage.embedding.cosine_distance(image_embedding)
            )
            .limit(limit)
        )
        return result.all()

    async def top_concepts_for_image(self, image_id: str, threshold: float = 0.2):
        e = Embedding
        ci = ConceptImage
        cs = ConceptImageSet
        c = Concept

        # embedding = await self.session.scalar(
        #     select(e.embedding).where(e.image_id == image_id)
        # )
        embedding = await self.get_image_embedding(image_id)

        # Distance expression
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