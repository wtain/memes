import base64
import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Optional

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.types.generated.facet import Schema as Facet
from Backend.app.types.generated.facetbucket import Schema as FacetBucket
from Backend.app.types.generated.meme import Schema as Meme
from Backend.app.types.generated.memetag import Schema as MemeTag
from Backend.app.types.generated.memesearchresponse import Schema as MemeSearchResponse
from graph.uf import UnionFind

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
            Facet(
                name=name,
                buckets=[FacetBucket(value=v, count=float(count)) for v, count in sorted(values.items())],
            )
            for name, values in raw_facet_map.items()
        ]

        facets.sort(key=lambda facet: facet.name)

        items = [
            Meme(id=str(r.id), imageUrl=f"/api/images/{r.id}", text=[], tags=[], originalFileName=r.filename)
            for r in rows
        ]

        await self._fill_texts_and_tags(items)

        has_next = len(items) > limit
        if has_next:
            items = items[:limit]

        next_cursor = self._encode_cursor(rows[-1]) if rows else None

        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=facets)

    async def _fill_texts_and_tags(self, items):
        ids = {meme.id for meme in items}
        index = {meme.id: meme for meme in items}
        for row in await self.repo.get_texts(ids):
            index[str(row.image_id)].text.append(f"{row.text} ({row.confidence})")
        for image_id, key, value, source in await self.repo.get_tags(ids):
            index[str(image_id)].tags.append(MemeTag(name=value, category=key, score=1, source=source))

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

    async def get_untagged(
            self,
            cursor: Optional[str],
            limit: int,
    ) -> MemeSearchResponse:
        cursor_created_at, cursor_id = self._decode_cursor(cursor)

        rows = await self.repo.get_untagged(
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
        )

        items = [
            Meme(id=str(r.id), imageUrl=f"/api/images/{r.id}", text=[], tags=[], originalFileName=r.filename)
            for r in rows
        ]

        await self._fill_texts_and_tags(items)

        has_next = len(items) > limit
        if has_next:
            items = items[:limit]

        next_cursor = self._encode_cursor(rows[-1]) if rows else None

        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=[])

    async def get_duplicates(
            self,
            cursor: Optional[str],
            limit: int,
            threshold: float
    ) -> MemeSearchResponse:
        # cursor_created_at, cursor_id = self._decode_cursor(cursor)
        cursor_created_at, cursor_id = self._decode_cursor(cursor)

        # ids_and_file_names = await self.repo.get_duplicates(
        ids_and_file_names = await self.repo.get_duplicates_precomputed(
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
            limit=limit,
            threshold=threshold
        )

        has_next = len(ids_and_file_names) > limit
        ids_and_file_names = ids_and_file_names[:limit]  # always trim,

        uf = UnionFind()

        file_names = {}
        created_at_dict = {}

        # for (id, image_id1, filename1, image_id2, filename2, created_at, distance,) in images:
        #     uf.connect(image_id1, image_id2)
        #     file_names[image_id1] = filename1
        #     file_names[image_id2] = filename2
        #     created_at_dict[image_id1] = created_at
        #     created_at_dict[image_id2] = created_at

        for (id, image_id1, filename1, image_id2, filename2, created_at, distance,) in ids_and_file_names:
            uf.connect(image_id1, image_id2)
            file_names[image_id1] = filename1
            file_names[image_id2] = filename2
            created_at_dict[image_id1] = created_at
            created_at_dict[image_id2] = created_at

        results = []
        for id1 in uf.list_clusters():
            for id2 in uf.get_cluster(id1):
                results.append((id2, file_names[id2], created_at_dict[id2]))

        if has_next and ids_and_file_names:
            last_id, _, _, _, _, last_created_at, _ = ids_and_file_names[limit - 1]
            next_cursor = self._encode_cursor1(last_created_at, last_id)
        else:
            next_cursor = None

        items = [
            Meme(id=str(id), imageUrl=f"/api/images/{id}", text=[], tags=[], originalFileName=filename)
            for (id, filename, created_at) in results
        ]

        # safe on any length
        await self._fill_texts_and_tags(items)

        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=[])

    async def get_duplicates_clustered(
            self,
            cursor: Optional[str],
            limit: int,
            threshold: float
    ) -> MemeSearchResponse:
        cursor_created_at, cursor_id = self._decode_cursor(cursor)

        images = await self.repo.get_duplicates_clustered(
            cursor_id=cursor_id,
            limit=limit,
        )

        has_next = len(images) > limit
        images = images[:limit]  # always trim,

        if has_next and images:
            last_id, _, last_created_at, _ = images[limit - 1]
            next_cursor = self._encode_cursor1(last_created_at, last_id)
        else:
            next_cursor = None

        items = [
            Meme(id=str(id), imageUrl=f"/api/images/{id}", text=[], tags=[], originalFileName=filename)
            for (id, filename, created_at, cluster_id, ) in images
        ]

        # safe on any length
        await self._fill_texts_and_tags(items)

        return MemeSearchResponse(items=items, nextCursor=next_cursor, hasNext=has_next, facets=[])

    async def mark_excluded(self, image_id):
        await self.repo.set_is_excluded(image_id, True)

    async def unmark_excluded(self, image_id):
        await self.repo.set_is_excluded(image_id, False)

    async def get_is_excluded(self, image_id) -> bool:
        return await self.repo.get_is_excluded(image_id)

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
        id = last_row.id
        created_at = last_row.created_at
        return ImageService._encode_cursor1(created_at, id)

    @staticmethod
    def _encode_cursor1(created_at, id):
        payload = json.dumps({"id": str(id), "created_at": created_at.isoformat()})
        return base64.urlsafe_b64encode(payload.encode()).decode()