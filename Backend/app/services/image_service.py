import base64
import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.repositories.image_repository import ImageRepository
from app.types.generated.facet import Schema as Facet
from app.types.generated.facetbucket import Schema as FacetBucket
from app.types.generated.meme import Schema as Meme
from app.types.generated.memetag import Schema as MemeTag
from app.types.generated.memesearchresponse import Schema as MemeSearchResponse


class ImageService:
    def __init__(self, repo: ImageRepository):
        self.repo = repo

    async def search(
        self,
        q: Optional[str],
        raw_facets: Optional[str],
        cursor: Optional[str],
        limit: int,
    ) -> MemeSearchResponse:
        tags = self._parse_facets(raw_facets)
        cursor_created_at, cursor_id = self._decode_cursor(cursor)

        rows, raw_facet_map = await self.repo.search(
            q=q,
            tags=tags,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
        )

        facets = [
            Facet(name=name,
                  buckets=[FacetBucket(value=v, count=0.0) for v in list(sorted(set(values)))]
            )
            for name, values in raw_facet_map.items()
        ]

        facets.sort(key=lambda facet: facet.name)

        items = [
            Meme(id=str(r.id), imageUrl=f"/api/images/{r.id}", text=[], tags=[], originalFileName=r.filename)
            for r in rows
        ]

        ids = {meme.id for meme in items}
        index = {meme.id: meme for meme in items}

        for row in await self.repo.get_texts(ids):
            index[str(row.image_id)].text.append(f"{row.text} ({row.confidence})")

        for image_id, key, value, source in await self.repo.get_tags(ids):
            index[str(image_id)].tags.append(MemeTag(name=value, category=key, score=1, source=source))

        has_next = len(items) > limit
        if has_next:
            items = items[:limit]

        next_cursor = self._encode_cursor(rows[-1]) if rows else None

        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=facets)

    async def get_meme(self, image_id: str) -> Meme:
        filename, texts, tags = await self.repo.get_meme_data(image_id)
        return Meme(
            id=image_id,
            imageUrl=f"/api/images/{image_id}",
            text=texts,
            tags=[MemeTag(name=value, category=key) for key, value in tags],
            originalFileName=filename,
        )

    async def get_similar(self, image_id: str) -> MemeSearchResponse:
        embedding = await self.repo.get_embedding(image_id)
        rows = await self.repo.get_similar(image_id, embedding.tolist())
        items = [
            Meme(id=str(iid), imageUrl=f"/api/images/{iid}", text=[], tags=[], originalFileName=fname)
            for iid, _, fname in rows
        ]
        return MemeSearchResponse(items=items)

    @staticmethod
    def _parse_facets(raw: Optional[str]) -> dict[str, set]:
        tags = defaultdict(set)
        if raw:
            for facet in raw.split(","):
                category, value = facet.split(":", 1)
                tags[category].add(value)
        return dict(tags)

    @staticmethod
    def _decode_cursor(cursor: Optional[str]):
        if not cursor:
            return None, None
        obj = json.loads(base64.urlsafe_b64decode(cursor).decode())
        return datetime.fromisoformat(obj["created_at"]), uuid.UUID(obj["id"])

    @staticmethod
    def _encode_cursor(last_row) -> str:
        payload = json.dumps({"id": str(last_row.id), "created_at": last_row.created_at.isoformat()})
        return base64.urlsafe_b64encode(payload.encode()).decode()