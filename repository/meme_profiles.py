import uuid
from dataclasses import dataclass
from typing import Collection

from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from Storage.models import (
    Image, ImageProcessingStatus, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue,
)

ID_CHUNK = 1000
PIPELINE = "meme_profile"   # equals batch.utils.meme_profile_config.PIPELINE (the backend image has no batch/); a test pins it
CATEGORIES = ("without_profile", "needs_review", "stale_taxonomy", "failed", "human_verified", "pending_vector")
AXES = ("emotion", "stance", "topic", "tone")


@dataclass(frozen=True)
class ProfileMeta:
    taxonomy_version: int
    extractor_model: str
    extractor_prompt_version: str
    inputs_fingerprint: str
    human_verified: bool = False


@dataclass(frozen=True)
class ProfileData:
    situation_text: str | None
    situation_confidence: float | None
    use_when: str | None
    values: dict[str, list[dict]]  # axis -> [{"key", "confidence", "source"}]

    @classmethod
    def from_validated(cls, validated: dict, use_when: str | None) -> "ProfileData":
        """Build from taxonomy.validate_profile's `profile` output: every list-valued key is an axis."""
        return cls(
            situation_text=validated.get("situation"),
            situation_confidence=validated.get("situationConfidence"),
            use_when=use_when,
            values={axis: list(items) for axis, items in validated.items() if isinstance(items, list)},
        )


@dataclass(frozen=True)
class ProfileView:
    image_id: uuid.UUID
    taxonomy_version: int
    human_verified: bool
    situation_text: str | None
    situation_confidence: float | None
    use_when: str | None
    values: dict[str, list[dict]]
    extracted_at: str
    updated_at: str


class MemeProfileRepository:
    """Async data access for meme_profiles and its two child tables. Never commits (CLAUDE.md): the
    caller's batch committer controls transaction boundaries."""

    def __init__(self, session):
        self.session = session

    async def get_meta(self, image_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProfileMeta]:
        metas: dict[uuid.UUID, ProfileMeta] = {}
        for start in range(0, len(image_ids), ID_CHUNK):
            chunk = image_ids[start:start + ID_CHUNK]
            result = await self.session.execute(
                select(
                    MemeProfile.image_id, MemeProfile.taxonomy_version, MemeProfile.extractor_model,
                    MemeProfile.extractor_prompt_version, MemeProfile.inputs_fingerprint, MemeProfile.human_verified,
                ).where(MemeProfile.image_id.in_(chunk))
            )
            for image_id, version, model, prompt_version, fingerprint, human in result.all():
                metas[image_id] = ProfileMeta(version, model, prompt_version, fingerprint, human)
        return metas

    async def set_lock_timeout(self, seconds: int) -> None:
        """Bound how long this transaction waits for a row lock (curator requests fail fast instead of hanging
        behind a long batch transaction). SET LOCAL ends with the transaction."""
        await self.session.execute(text(f"SET LOCAL lock_timeout = '{int(seconds)}s'"))

    async def _lock_profile(self, image_id: uuid.UUID, meta: ProfileMeta) -> tuple[MemeProfile, bool]:
        """Serialise writers of one image's profile. Makes sure the row exists (a placeholder insert that does
        nothing when it already does), then locks it with SELECT ... FOR UPDATE and reloads it, so every
        following statement of this transaction sees what a concurrent writer committed while we waited.
        Returns the locked row and whether this very call created it."""
        inserted = (await self.session.execute(
            insert(MemeProfile)
            .values(image_id=image_id, taxonomy_version=meta.taxonomy_version, extractor_model=meta.extractor_model,
                    extractor_prompt_version=meta.extractor_prompt_version,
                    inputs_fingerprint=meta.inputs_fingerprint, human_verified=False)
            .on_conflict_do_nothing(index_elements=["image_id"])
            .returning(MemeProfile.image_id)
        )).first() is not None
        row = (await self.session.execute(
            select(MemeProfile).where(MemeProfile.image_id == image_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one()
        return row, inserted

    async def save_profile(self, image_id: uuid.UUID, data: ProfileData, meta: ProfileMeta) -> bool:
        """Replace one image's model-generated profile. Returns False (and writes nothing) when the
        existing row is human-verified; that check runs after the row lock, so a human save wins."""
        row, created = await self._lock_profile(image_id, meta)
        if row.human_verified:
            return False
        await self._write(row, created, data, meta, human=False)
        return True

    async def save_human_profile(self, image_id: uuid.UUID, data: ProfileData, meta: ProfileMeta) -> None:
        """Write a human-edited profile: every value gets source 'human' and the row is human_verified."""
        row, created = await self._lock_profile(image_id, meta)
        await self._write(row, created, data, meta, human=True)

    async def clear_failure(self, image_id: uuid.UUID) -> bool:
        """Delete this pipeline's failure record of the image (and no other pipeline's)."""
        result = await self.session.execute(delete(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image_id, ImageProcessingStatus.pipeline == PIPELINE))
        return result.rowcount > 0

    async def _write(self, row: MemeProfile, created: bool, data: ProfileData, meta: ProfileMeta,
                     *, human: bool) -> None:
        image_id = row.image_id
        situation_changed = created or row.situation_text != data.situation_text
        if not created:
            await self.session.execute(delete(MemeProfileValue).where(MemeProfileValue.image_id == image_id))
        row.taxonomy_version = meta.taxonomy_version
        row.extractor_model = meta.extractor_model
        row.extractor_prompt_version = meta.extractor_prompt_version
        row.inputs_fingerprint = meta.inputs_fingerprint
        row.situation_text = data.situation_text
        row.situation_confidence = data.situation_confidence
        row.use_when = data.use_when
        row.human_verified = human
        row.extracted_at = func.now()
        row.updated_at = func.now()
        await self.session.flush()
        if situation_changed:
            await self.session.execute(
                delete(MemeProfileSituationEmbedding).where(MemeProfileSituationEmbedding.image_id == image_id))
        for axis, items in data.values.items():
            for item in items:
                self.session.add(MemeProfileValue(
                    image_id=image_id, axis=axis, value_key=item["key"], confidence=item["confidence"],
                    source="human" if human else item.get("source", "model"),
                ))

    async def delete_model_generated(self, image_ids: Collection[uuid.UUID] | None = None) -> int:
        """`--reset`: delete every profile that is not human-verified (child rows cascade in the database),
        optionally only those of the given images."""
        statement = delete(MemeProfile).where(MemeProfile.human_verified.is_(False))
        if image_ids is not None:
            statement = statement.where(MemeProfile.image_id.in_(list(image_ids)))
        result = await self.session.execute(statement)
        return result.rowcount

    async def delete_all_embeddings(self, image_ids: Collection[uuid.UUID] | None = None) -> int:
        statement = delete(MemeProfileSituationEmbedding)
        if image_ids is not None:
            statement = statement.where(MemeProfileSituationEmbedding.image_id.in_(list(image_ids)))
        result = await self.session.execute(statement)
        return result.rowcount

    async def get_profiles_needing_embedding(self, model_name: str, model_digest: str,
                                             image_ids: Collection[uuid.UUID] | None = None
                                             ) -> list[tuple[uuid.UUID, str]]:
        """Profiles with a non-blank situation_text and no embedding row, or one from a different model or
        digest, optionally only among the given images."""
        statement = (
            select(MemeProfile.image_id, MemeProfile.situation_text)
            .outerjoin(MemeProfileSituationEmbedding, MemeProfileSituationEmbedding.image_id == MemeProfile.image_id)
            .where(
                MemeProfile.situation_text.is_not(None),
                func.length(func.trim(MemeProfile.situation_text)) > 0,
                or_(
                    MemeProfileSituationEmbedding.image_id.is_(None),
                    MemeProfileSituationEmbedding.model_name != model_name,
                    MemeProfileSituationEmbedding.model_digest != model_digest,
                ),
            )
            .order_by(MemeProfile.image_id)
        )
        if image_ids is not None:
            statement = statement.where(MemeProfile.image_id.in_(list(image_ids)))
        result = await self.session.execute(statement)
        return [(image_id, text) for image_id, text in result.all()]

    async def save_situation_embedding(self, image_id: uuid.UUID, vector: list[float],
                                       model_name: str, model_digest: str) -> None:
        stmt = insert(MemeProfileSituationEmbedding).values(
            image_id=image_id, embedding=vector, model_name=model_name, model_digest=model_digest)
        stmt = stmt.on_conflict_do_update(
            index_elements=["image_id"],
            set_={"embedding": stmt.excluded.embedding, "model_name": stmt.excluded.model_name,
                  "model_digest": stmt.excluded.model_digest, "created_at": func.now()},
        )
        await self.session.execute(stmt)

    async def coverage(self, *, taxonomy_version: int, prompt_version: str,
                       stale_image_ids=frozenset(), failed_image_ids=frozenset()) -> dict:
        """Counts for run stats and the later admin view. `stale_image_ids` carries the staleness the
        database cannot see (fingerprint or model change), computed by the extraction job's selection."""
        active = (await self.session.execute(
            select(func.count()).select_from(Image).where(Image.status == "active"))).scalar_one()
        rows = (await self.session.execute(
            select(MemeProfile.image_id, MemeProfile.taxonomy_version,
                   MemeProfile.extractor_prompt_version, MemeProfile.human_verified)
            .join(Image, Image.id == MemeProfile.image_id)
            .where(Image.status == "active")
        )).all()
        stale = needs_review = human = 0
        for image_id, version, prompt, human_verified in rows:
            if human_verified:
                is_stale = version != taxonomy_version
            else:
                is_stale = version != taxonomy_version or prompt != prompt_version or image_id in stale_image_ids
            stale += is_stale
            human += human_verified
            needs_review += is_stale and human_verified
        failed_ids = list(failed_image_ids)
        failed = 0
        for start in range(0, len(failed_ids), 1000):
            chunk = failed_ids[start:start + 1000]
            failed += (await self.session.execute(
                select(func.count()).select_from(Image)
                .where(Image.status == "active", Image.id.in_(chunk)))).scalar_one()
        return {"active": active, "profiled": len(rows), "stale": stale, "needs_review": needs_review,
                "human_verified": human, "failed": failed}

    async def image_is_active(self, image_id: uuid.UUID) -> bool:
        found = await self.session.execute(select(Image.id).where(Image.id == image_id, Image.status == "active"))
        return found.first() is not None

    async def get_view(self, image_id: uuid.UUID) -> "ProfileView | None":
        row = (await self.session.execute(
            select(MemeProfile).where(MemeProfile.image_id == image_id).execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if row is None:
            return None
        items = (await self.session.execute(
            select(MemeProfileValue.axis, MemeProfileValue.value_key, MemeProfileValue.confidence,
                   MemeProfileValue.source)
            .where(MemeProfileValue.image_id == image_id)
            .order_by(MemeProfileValue.confidence.desc(), MemeProfileValue.value_key)
        )).all()
        values: dict[str, list[dict]] = {axis: [] for axis in AXES}
        for axis, key, confidence, source in items:
            values[axis].append({"key": key, "confidence": confidence, "source": source})
        return ProfileView(
            image_id=row.image_id, taxonomy_version=row.taxonomy_version, human_verified=bool(row.human_verified),
            situation_text=row.situation_text, situation_confidence=row.situation_confidence, use_when=row.use_when,
            values=values, extracted_at=row.extracted_at.isoformat(), updated_at=row.updated_at.isoformat())

    async def vector_status(self, image_id: uuid.UUID, model_name: str) -> str:
        situation = (await self.session.execute(
            select(MemeProfile.situation_text).where(MemeProfile.image_id == image_id))).scalar_one_or_none()
        if situation is None or not situation.strip():
            return "none"
        stored = (await self.session.execute(
            select(MemeProfileSituationEmbedding.model_name)
            .where(MemeProfileSituationEmbedding.image_id == image_id))).scalar_one_or_none()
        return "fresh" if stored == model_name else "pending"

    async def confirm(self, image_id: uuid.UUID, taxonomy_version: int) -> bool:
        """Mark the profile human-verified without touching its values, their sources or its vector."""
        result = await self.session.execute(
            update(MemeProfile).where(MemeProfile.image_id == image_id)
            .values(human_verified=True, taxonomy_version=taxonomy_version, updated_at=func.now()))
        return result.rowcount > 0

    async def reset(self, image_id: uuid.UUID) -> bool:
        """Delete the profile (values and vector cascade) and this pipeline's failure record, so the next
        build_meme_profiles run re-extracts the image."""
        deleted = await self.session.execute(delete(MemeProfile).where(MemeProfile.image_id == image_id))
        await self.session.execute(delete(ImageProcessingStatus).where(
            ImageProcessingStatus.image_id == image_id, ImageProcessingStatus.pipeline == PIPELINE))
        return deleted.rowcount > 0

    def _category_query(self, category: str, taxonomy_version: int, model_name: str):
        base = (select(Image.id, func.coalesce(MemeProfile.human_verified, False).label("human"))
                .select_from(Image).outerjoin(MemeProfile, MemeProfile.image_id == Image.id)
                .where(Image.status == "active"))
        if category == "without_profile":
            return base.where(MemeProfile.image_id.is_(None))
        if category == "needs_review":
            return base.where(MemeProfile.human_verified.is_(True), MemeProfile.taxonomy_version != taxonomy_version)
        if category == "stale_taxonomy":
            return base.where(MemeProfile.human_verified.is_(False), MemeProfile.taxonomy_version != taxonomy_version)
        if category == "human_verified":
            return base.where(MemeProfile.human_verified.is_(True))
        if category == "failed":
            return base.where(exists().where(
                ImageProcessingStatus.image_id == Image.id, ImageProcessingStatus.pipeline == PIPELINE,
                ImageProcessingStatus.status == "failed"))
        if category == "pending_vector":
            vector = MemeProfileSituationEmbedding
            return (base.outerjoin(vector, vector.image_id == Image.id)
                    .where(MemeProfile.situation_text.is_not(None),
                           func.length(func.trim(MemeProfile.situation_text)) > 0,
                           or_(vector.image_id.is_(None), vector.model_name != model_name)))
        raise ValueError(f"unknown category {category!r}")

    async def _count_category(self, category: str, taxonomy_version: int, model_name: str) -> int:
        query = self._category_query(category, taxonomy_version, model_name).subquery()
        return (await self.session.execute(select(func.count()).select_from(query))).scalar_one()

    async def live_counts(self, taxonomy_version: int, model_name: str) -> dict:
        active = (await self.session.execute(
            select(func.count()).select_from(Image).where(Image.status == "active"))).scalar_one()
        counts = {category: await self._count_category(category, taxonomy_version, model_name)
                  for category in CATEGORIES}
        return {"active": active, "profiled": active - counts["without_profile"], **counts}

    async def list_category(self, category: str, taxonomy_version: int, model_name: str,
                            after: uuid.UUID | None, limit: int) -> tuple[list[tuple[uuid.UUID, bool]], int]:
        total = await self._count_category(category, taxonomy_version, model_name)
        page = self._category_query(category, taxonomy_version, model_name).order_by(Image.id)
        if after is not None:
            page = page.where(Image.id > after)
        rows = (await self.session.execute(page.limit(limit))).all()
        return [(image_id, bool(human)) for image_id, human in rows], total
