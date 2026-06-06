from sqlalchemy import delete, select

from Storage.models import OllamaDescription


class OllamaDescriptionsRepository:

    def __init__(self, session):
        self.session = session

    async def delete_all(self) -> None:
        await self.session.execute(delete(OllamaDescription))

    def save(self, image_id, text: str) -> None:
        self.session.add(OllamaDescription(image_id=image_id, text=text))

    async def get_all_texts(self) -> list[str]:
        result = await self.session.execute(select(OllamaDescription.text))
        return result.scalars().all()