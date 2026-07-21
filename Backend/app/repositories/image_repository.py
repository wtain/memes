from collections import defaultdict
from datetime import datetime
from typing import Optional
import uuid

import sqlalchemy
from sqlalchemy import select, tuple_, distinct, and_, union_all, func, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import (
    Image, OCRText, Embedding, ImageTag, ImageExtras, TmpDuplicates, TmpImageClusters,
    ImageDescription, ImageDescriptionEmbedding, ImageDescriptionFeedback,
)
from graph.uf import UnionFind
from repository.ocr_lemmas import matching_image_ids


class ImageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _build_filtered_ids_query(
        self,
        q: Optional[str],
        tags: dict[str, set],
    ):
        """Returns a scalar-subquery of image IDs matching q and tags, unpaginated."""
        img = aliased(Image)
        image_tag = aliased(ImageTag)

        query = select(img.id)

        matching_ids = await matching_image_ids(self.session, q)
        if matching_ids is not None:
            query = query.where(img.id.in_(matching_ids))

        if tags:
            tag_queries = [
                select(distinct(image_tag.image_id)).where(
                    and_(image_tag.key == key, image_tag.value.in_(values))
                )
                for key, values in tags.items()
            ]
            tags_result = await self.session.execute(union_all(*tag_queries))
            query = query.where(img.id.in_([id for (id,) in tags_result.all()]))

        return query

    async def search(
        self,
        q: Optional[str],
        tags: dict[str, set],
        cursor_created_at: Optional[datetime],
        cursor_id: Optional[uuid.UUID],
        limit: int,
    ):
        img = aliased(Image)
        image_tag = aliased(ImageTag)

        filtered_ids = await self._build_filtered_ids_query(q, tags)
        filtered_ids_subquery = filtered_ids.subquery()

        # Facet counts over the full filtered set — no pagination applied here
        facets_query = (
            select(
                image_tag.key,
                image_tag.value,
                sqlalchemy.func.count(image_tag.image_id).label("count"),
            )
            .where(image_tag.image_id.in_(select(filtered_ids_subquery.c.id)))
            .group_by(image_tag.key, image_tag.value)
        )
        facets_result = await self.session.execute(facets_query)
        raw_facets: dict[str, dict[str, int]] = defaultdict(dict)
        for k, v, count in facets_result.all():
            raw_facets[k][v] = count

        # Paginated page of image rows with flagged status
        extras = aliased(ImageExtras)
        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(img.id.in_(select(filtered_ids_subquery.c.id)))
        )
        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id)
            )

        results = await self.session.execute(
            query.order_by(img.created_at.desc(), img.id.desc()).limit(limit + 1)
        )

        return results.all(), dict(raw_facets)

    async def get_filename(self, image_id: str) -> Optional[str]:
        result = await self.session.execute(
            select(Image.filename).where(Image.id == image_id)
        )
        return result.scalar_one_or_none()

    async def get_texts(self, image_ids: set[str], min_confidence: float = 0.8):
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence)
            .where(OCRText.image_id.in_(image_ids), OCRText.confidence > min_confidence)
        )
        return result.all()

    async def get_tags(self, image_ids: set[str]):
        result = await self.session.execute(
            select(ImageTag.image_id, ImageTag.key, ImageTag.value, ImageTag.source)
            .where(ImageTag.image_id.in_(image_ids))
        )
        return result.all()

    async def get_embedding(self, image_id: str):
        result = await self.session.execute(
            select(Embedding.embedding).where(Embedding.image_id == image_id)
        )
        return result.scalars().first()

    async def get_similar(self, image_id: str, embedding, limit: int = 10):
        img = aliased(Image)
        embed = aliased(Embedding)
        extras = aliased(ImageExtras)
        result = await self.session.execute(
            select(embed.image_id, embed.embedding.cosine_distance(embedding), img.filename, extras.flagged)
            .join(img, img.id == embed.image_id)
            .outerjoin(extras, img.id == extras.image_id)
            .filter(embed.image_id != image_id)
            .order_by(embed.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return result.all()

    async def get_similar_by_description(self, image_id: str, limit: int = 10):
        source_desc, source_emb = aliased(ImageDescription), aliased(ImageDescriptionEmbedding)
        cand_desc, cand_emb = aliased(ImageDescription), aliased(ImageDescriptionEmbedding)
        img, extras = aliased(Image), aliased(ImageExtras)

        result = await self.session.execute(
            select(
                cand_desc.image_id,
                func.min(source_emb.embedding.cosine_distance(cand_emb.embedding)).label("distance"),
                img.filename,
                extras.flagged,
            )
            .select_from(source_desc)
            .join(source_emb, source_emb.image_description_id == source_desc.id)
            .join(cand_desc, cand_desc.prompt_key == source_desc.prompt_key)
            .join(cand_emb, cand_emb.image_description_id == cand_desc.id)
            .join(img, img.id == cand_desc.image_id)
            .outerjoin(extras, extras.image_id == cand_desc.image_id)
            .where(source_desc.image_id == image_id, cand_desc.image_id != image_id)
            .group_by(cand_desc.image_id, img.filename, extras.flagged)
            .order_by("distance")
            .limit(limit)
        )
        return result.all()

    async def has_description_embedding(self, image_id: str) -> bool:
        result = await self.session.execute(
            select(ImageDescriptionEmbedding.image_description_id)
            .join(ImageDescription, ImageDescription.id == ImageDescriptionEmbedding.image_description_id)
            .where(ImageDescription.image_id == image_id)
            .limit(1)
        )
        return result.first() is not None

    async def get_descriptions(self, image_id: str):
        result = await self.session.execute(
            select(
                ImageDescription.prompt_key,
                ImageDescription.text,
                ImageDescription.model_used,
                ImageDescription.created_at,
                ImageDescriptionFeedback.approved,
            )
            .outerjoin(
                ImageDescriptionFeedback,
                ImageDescriptionFeedback.image_description_id == ImageDescription.id,
            )
            .where(ImageDescription.image_id == image_id)
            .order_by(ImageDescription.prompt_key)
        )
        return result.all()

    async def get_description_id(self, image_id: str, prompt_key: str) -> Optional[uuid.UUID]:
        result = await self.session.execute(
            select(ImageDescription.id)
            .where(ImageDescription.image_id == image_id, ImageDescription.prompt_key == prompt_key)
        )
        return result.scalar_one_or_none()

    async def get_description_feedback(self, description_id) -> Optional[bool]:
        result = await self.session.execute(
            select(ImageDescriptionFeedback.approved)
            .where(ImageDescriptionFeedback.image_description_id == description_id)
        )
        return result.scalar_one_or_none()

    async def set_description_feedback(self, description_id, approved: bool) -> None:
        stmt = (
            insert(ImageDescriptionFeedback)
            .values(image_description_id=description_id, approved=approved)
            .on_conflict_do_update(
                index_elements=["image_description_id"],
                set_={"approved": approved},
            )
        )
        await self.session.execute(stmt)

    async def clear_description_feedback(self, description_id) -> None:
        await self.session.execute(
            delete(ImageDescriptionFeedback)
            .where(ImageDescriptionFeedback.image_description_id == description_id)
        )

    async def get_meme_data(self, image_id: str):
        filename = await self.get_filename(image_id)

        texts_result = await self.session.execute(
            select(
                OCRText.text
            )
            .where(
                OCRText.image_id == image_id
            )
        )
        texts = [t for (t,) in texts_result]

        tags_result = await self.session.execute(
            select(ImageTag.key, ImageTag.value).where(ImageTag.image_id == image_id)
        )
        tags = tags_result.all()

        return filename, texts, tags

    async def get_untagged(
            self,
            cursor_created_at: Optional[datetime],
            cursor_id: Optional[uuid.UUID],
            limit: int,
    ):
        img = aliased(Image)
        image_tag = aliased(ImageTag)
        extras = aliased(ImageExtras)

        exists_subquery = (
            select(image_tag.image_id)
            .where(image_tag.image_id == img.id)
            .correlate(img)
            .exists()
        )

        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(~exists_subquery)
        )

        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id)
            )

        results = await self.session.execute(
            query.order_by(img.created_at.desc(), img.id.desc()).limit(limit + 1)
        )
        return results.all()

    async def get_no_ocr(
            self,
            cursor_created_at: Optional[datetime],
            cursor_id: Optional[uuid.UUID],
            limit: int,
    ):
        img = aliased(Image)
        ocr = aliased(OCRText)
        extras = aliased(ImageExtras)

        exists_subquery = (
            select(ocr.image_id)
            .where(ocr.image_id == img.id)
            .correlate(img)
            .exists()
        )

        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .outerjoin(extras, img.id == extras.image_id)
            .where(~exists_subquery)
        )

        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id)
            )

        results = await self.session.execute(
            query.order_by(img.created_at.desc(), img.id.desc()).limit(limit + 1)
        )
        return results.all()

    # slow? index?
    async def get_duplicates(self,
                             cursor_created_at: Optional[datetime],
                             cursor_id: Optional[uuid.UUID],
                             limit: int,
                             threshold: float):
        img1 = aliased(Image)
        embed1 = aliased(Embedding)
        img2 = aliased(Image)
        embed2 = aliased(Embedding)
        query = (
            select(
                img1.id,
                img1.filename,
                img1.created_at,
                img2.id,
                img2.filename,
                img2.created_at,
                embed1.embedding.cosine_distance(embed2.embedding).label("distance")
            )
            .select_from(img1, img2)
            .join(
                embed1, embed1.image_id == img1.id
            )
            .join(
                embed2, embed2.image_id == img2.id
            )
            .where(
                and_(
                    embed1.embedding.cosine_distance(embed2.embedding) < threshold,
                    img1.id < img2.id # avoid duplicates a <=> b and b <=> a
                )
            )
        )

        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img1.created_at, img1.id) < tuple_(cursor_created_at, cursor_id)
            )

        images = await self.session.execute(
            query.order_by(img1.created_at.desc(), img1.id.desc()).limit(limit + 1)
        )
        uf = UnionFind()

        file_names = {}
        created_at = {}

        for (id1, filename1, created1, id2, filename2, created2, distance,) in images:
            uf.connect(id1, id2)
            file_names[id1] = filename1
            file_names[id2] = filename2
            created_at[id1] = created1
            created_at[id2] = created2

        results = []
        for id1 in uf.list_clusters():
            for id2 in uf.get_cluster(id1):
                results.append((id2, file_names[id2], created_at[id2]))

        return results

    # copy of the above
    async def get_duplicates_precomputed(self,
                             cursor_created_at: Optional[datetime],
                             cursor_id: Optional[int],
                             limit: int,
                             threshold: float):
        dups = aliased(TmpDuplicates)
        img1 = aliased(Image)
        img2 = aliased(Image)
        query = (
            select(
                dups.id,
                dups.image_id1,
                img1.filename,
                dups.image_id2,
                img2.filename,
                dups.created_at,
                dups.distance,
            )
            .join(
                img1, img1.id == dups.image_id1
            )
            .join(
                img2, img2.id == dups.image_id2
            )
            .where(
                and_(
                    dups.distance < threshold,
                )
            )
        )

        if cursor_id:
            query = query.where(
                dups.id < cursor_id
            )

        images = await self.session.execute(
            query.order_by(dups.id.desc()).limit(limit + 1)
        )

        return [(id, image_id1, filename1, image_id2, filename2, created_at, distance,) for (id, image_id1, filename1, image_id2, filename2, created_at, distance,) in images]

    async def get_duplicates_clustered(self,
                             after_cluster_id: Optional[int],
                             limit: int,):
        img = aliased(Image)
        cluster = aliased(TmpImageClusters)
        extras = aliased(ImageExtras)

        query = (
            select(
                img.id,
                img.filename,
                img.created_at,
                cluster.cluster_id,
                extras.flagged,
            )
            .join(cluster, cluster.image_id == img.id)
            .outerjoin(extras, img.id == extras.image_id)
        )

        if after_cluster_id is not None:
            query = query.where(cluster.cluster_id > after_cluster_id)

        images = await self.session.execute(
            query.order_by(
                cluster.cluster_id,
                img.id,
           ).limit(limit + 1)
        )

        return [(id, filename, created_at, cluster_id, flagged, ) for (id, filename, created_at, cluster_id, flagged,) in images]


    async def get_flagged(
            self,
            cursor_created_at: Optional[datetime],
            cursor_id: Optional[uuid.UUID],
            limit: int,
    ):
        img = aliased(Image)
        extras = aliased(ImageExtras)

        query = (
            select(img.id, img.filename, img.created_at, extras.flagged)
            .join(extras, img.id == extras.image_id)
            .where(extras.flagged == True)
        )

        if cursor_created_at and cursor_id:
            query = query.where(
                tuple_(img.created_at, img.id) < tuple_(cursor_created_at, cursor_id)
            )

        results = await self.session.execute(
            query.order_by(img.created_at.desc(), img.id.desc()).limit(limit + 1)
        )
        return results.all()

    async def set_flagged(self, image_id, is_flagged):
        stmt = (
            insert(ImageExtras)
            .values(image_id=image_id, flagged=is_flagged)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"flagged": is_flagged},
            )
        )
        await self.session.execute(stmt)

    async def get_is_flagged(self, image_id) -> bool:
        query = (
            select(
                ImageExtras
            )
            .where(
                ImageExtras.image_id == image_id
            )
        )
        result = await self.session.execute(query)
        extras = result.scalar_one_or_none()
        return extras and extras.flagged
