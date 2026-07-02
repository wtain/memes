from sqlalchemy import delete, select, func, true
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import Concept, ConceptImage, ConceptImageSet, Embedding, Image


class ConceptRepositoryBase:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_all(self):
        result = await self.session.execute(select(Concept.id, Concept.name))
        return result.all()

    async def top_images_for_concept(self, concept_id: int, threshold: float = 0.2, limit: int = 50):
        ci = ConceptImage
        cis = ConceptImageSet
        i = Image
        e = Embedding

        concept_embeddings_subq = (
            select(ci.embedding.label("embedding"))
            .join(cis, ci.concept_image_set_id == cis.id)
            .where(cis.concept_id == concept_id)
            .subquery()
        )

        ce = concept_embeddings_subq.c.embedding

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
        distance = ie.embedding.cosine_distance(ce)

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


class ConceptsRepository(ConceptRepositoryBase):

    async def delete_all(self):
        print("Deleting all concept embeddings...")
        await self.session.execute(delete(Concept))
        await self.session.commit()
        print("Done")

    def add(self, name, embedding):
        self.session.add(Concept(name=name, embedding=embedding))

    async def get_all_with_embeddings(self):
        result = await self.session.execute(
            select(Concept.id, Concept.name, Concept.embedding)
            .where(Concept.embedding.isnot(None))
        )
        return result.all()