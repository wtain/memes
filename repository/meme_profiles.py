import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from Storage.models import Image, MemeProfile, MemeProfileSituationEmbedding, MemeProfileValue

ID_CHUNK = 1000


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

    async def save_profile(self, image_id: uuid.UUID, data: ProfileData, meta: ProfileMeta) -> bool:
        """Replace one image's model-generated profile. Returns False (and writes nothing) when the
        existing row is human-verified."""
        existing = await self.session.get(MemeProfile, image_id)
        if existing is not None and existing.human_verified:
            return False
        await self._write(image_id, existing, data, meta, human=False)
        return True

    async def save_human_profile(self, image_id: uuid.UUID, data: ProfileData, meta: ProfileMeta) -> None:
        """Write a human-edited profile: every value gets source 'human' and the row is human_verified."""
        existing = await self.session.get(MemeProfile, image_id)
        await self._write(image_id, existing, data, meta, human=True)

    async def _write(self, image_id, existing, data: ProfileData, meta: ProfileMeta, *, human: bool) -> None:
        situation_changed = existing is None or existing.situation_text != data.situation_text
        if existing is None:
            row = MemeProfile(image_id=image_id)
            self.session.add(row)
        else:
            row = existing
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

    async def delete_model_generated(self) -> int:
        """`--reset`: delete every profile that is not human-verified (child rows cascade in the database)."""
        result = await self.session.execute(delete(MemeProfile).where(MemeProfile.human_verified.is_(False)))
        return result.rowcount

    async def delete_all_embeddings(self) -> int:
        result = await self.session.execute(delete(MemeProfileSituationEmbedding))
        return result.rowcount

    async def get_profiles_needing_embedding(self, model_name: str, model_digest: str) -> list[tuple[uuid.UUID, str]]:
        """Profiles with a non-blank situation_text and no embedding row, or one from a different model or digest."""
        result = await self.session.execute(
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
            is_stale = version != taxonomy_version or prompt != prompt_version or image_id in stale_image_ids
            stale += is_stale
            human += human_verified
            needs_review += is_stale and human_verified
        return {"active": active, "profiled": len(rows), "stale": stale, "needs_review": needs_review,
                "human_verified": human, "failed": len(failed_image_ids)}
