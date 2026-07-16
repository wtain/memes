from sqlalchemy import delete, select

from Storage.models import ImageDescription, ImageDescriptionEmbedding


class ImageDescriptionEmbeddingsRepository:

    def __init__(self, session):
        self.session = session

    async def get_descriptions_without_embedding(self):
        has_embedding = select(ImageDescriptionEmbedding.image_description_id).distinct().scalar_subquery()
        result = await self.session.execute(
            select(ImageDescription.id, ImageDescription.text)
            .where(ImageDescription.id.not_in(has_embedding))
        )
        return result.all()

    def save(self, description_id, embedding: list[float]) -> None:
        self.session.add(ImageDescriptionEmbedding(
            image_description_id=description_id, embedding=embedding,
        ))

    async def delete_all(self) -> None:
        await self.session.execute(delete(ImageDescriptionEmbedding))
