from app.repositories.concept_repository import ConceptRepository
from app.types.generated.concept import Schema as ConceptDto
from app.types.generated.meme import Schema as Meme
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse


class ConceptService:
    def __init__(self, repo: ConceptRepository):
        self.repo = repo

    async def get_all(self) -> list[ConceptDto]:
        rows = await self.repo.get_all()
        return [ConceptDto(id=id, name=name) for id, name in rows]

    async def get_by_id(self, concept_id: int) -> ConceptDto:
        id, name = await self.repo.get_by_id(concept_id)
        return ConceptDto(id=id, name=name)

    async def get_top_images(self, concept_id: int) -> MemeSearchResponse:
        # embedding = await self.repo.get_embedding(concept_id)
        # rows = await self.repo.get_top_images(embedding)
        # items = [
        #     Meme(id=str(iid), imageUrl=f"/api/images/{iid}", text=[], tags=[], originalFileName=fname)
        #     for iid, _, fname in rows
        # ]
        rows = await self.repo.top_images_for_concept(concept_id)
        items = [
            Meme(id=str(iid), imageUrl=f"/api/images/{iid}", text=[], tags=[], originalFileName=fname)
            for iid, fname, _, _, _ in rows
        ]

        return MemeSearchResponse(items=items)

    async def get_for_image(self, image_id: str) -> list[ConceptDto]:
        # embedding = await self.repo.get_image_embedding(image_id)
        # rows = await self.repo.get_for_image(embedding)
        # return [ConceptDto(id=id, name=name) for (image_id, id, name, cid, cname, ) in rows]
        rows = await self.repo.top_concepts_for_image(image_id)
        return [ConceptDto(id=id, name=name) for (name, id, avg_distance, ) in rows]