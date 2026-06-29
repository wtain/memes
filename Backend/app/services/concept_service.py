from Backend.app.repositories.concept_repository import ConceptRepository
from Backend.app.types.generated.concept import Schema as ConceptDto
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse


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
        rows = await self.repo.top_images_for_concept(concept_id)
        items = [
            Meme(id=str(iid), imageUrl=f"/api/images/{iid}", text=[], tags=[], originalFileName=fname)
            for iid, fname, _, _, _ in rows
        ]

        return MemeSearchResponse(items=items)

    async def get_for_image(self, image_id: str) -> list[ConceptDto]:
        rows = await self.repo.top_concepts_for_image(image_id)
        return [ConceptDto(id=id, name=name) for (name, id, avg_distance, ) in rows]