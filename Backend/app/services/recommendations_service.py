import base64
import hashlib
import json
from typing import Optional

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.repositories.recommendations_repository import RecommendationsRepository
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse
from Backend.app.types.generated.memetag import Schema as MemeTag


class RecommendationsService:
    def __init__(self, repo: RecommendationsRepository, image_repo: ImageRepository):
        self.repo = repo
        self.image_repo = image_repo

    async def get_recommendations(
        self,
        q: Optional[str],
        seed: int,
        last_hash: Optional[str],
        limit: int,
    ) -> MemeSearchResponse:
        rows = await self.repo.get_recommendations(q=q, seed=seed, last_hash=last_hash, limit=limit)

        has_next = len(rows) > limit
        rows = rows[:limit]

        items = [
            Meme(
                id=str(r.id),
                imageUrl=f"/api/images/{r.id}",
                text=[],
                tags=[],
                originalFileName=r.filename,
                flagged=r.flagged if r.flagged is not None else False,
            )
            for r in rows
        ]

        await self._fill_texts_and_tags(items)

        next_cursor = None
        if has_next and rows:
            last_hash_val = hashlib.md5((str(rows[-1].id) + str(seed)).encode()).hexdigest()
            next_cursor = self._encode_cursor(seed, last_hash_val)

        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=[])

    async def _fill_texts_and_tags(self, items: list[Meme]) -> None:
        ids = {meme.id for meme in items}
        if not ids:
            return
        index = {meme.id: meme for meme in items}
        for row in await self.image_repo.get_texts(ids):
            index[str(row.image_id)].text.append(row.text)
        for image_id, key, value, source in await self.image_repo.get_tags(ids):
            index[str(image_id)].tags.append(MemeTag(name=value, category=key, score=1, source=source))

    @staticmethod
    def _encode_cursor(seed: int, last_hash: str) -> str:
        payload = json.dumps({"seed": seed, "last_hash": last_hash})
        return base64.urlsafe_b64encode(payload.encode()).decode()

    @staticmethod
    def decode_cursor(cursor: str) -> tuple[int, str]:
        obj = json.loads(base64.urlsafe_b64decode(cursor).decode())
        return obj["seed"], obj["last_hash"]