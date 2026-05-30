from sqlalchemy import delete

from Storage.models import Concept


class ConceptsRepository:

    def __init__(self, session):
        self.session = session


    async def delete_all(self):
        print("Deleting all concept embeddings...")
        await self.session.execute(
            delete(Concept)
        )
        await self.session.commit()
        print("Done")


    def add(self, name, embedding):
        self.session.add(Concept(name=name, embedding=embedding))