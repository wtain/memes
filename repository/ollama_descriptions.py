from sqlalchemy import delete

from Storage.models import OllamaDescription


class OllamaDescriptionsRepository:

    def __init__(self, session):
        self.session = session

    async def delete_all(self) -> None:
        await self.session.execute(delete(OllamaDescription))

    def save(self, image_id, text: str) -> None:
        self.session.add(OllamaDescription(image_id=image_id, text=text))