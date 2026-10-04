"""A snapshot of the profile state of the corpus, recorded in every baseline (P6 spec, decision 8)."""
import uuid
from typing import Sequence

from sqlalchemy import func, select

from Storage.models import MemeProfile, MemeProfileSituationEmbedding


async def collect_profile_state(session, corpus_ids: Sequence[str]) -> dict:
    ids = [uuid.UUID(image_id) for image_id in corpus_ids]

    async def counted(column, id_column) -> dict[str, int]:
        rows = await session.execute(
            select(column, func.count()).where(id_column.in_(ids)).group_by(column).order_by(column))
        return {str(key): count for key, count in rows.all()}

    profiled = (await session.execute(
        select(func.count()).select_from(MemeProfile).where(MemeProfile.image_id.in_(ids)))).scalar_one()
    embedded = (await session.execute(
        select(func.count()).select_from(MemeProfileSituationEmbedding)
        .where(MemeProfileSituationEmbedding.image_id.in_(ids)))).scalar_one()
    embedding_rows = await session.execute(
        select(MemeProfileSituationEmbedding.model_name, MemeProfileSituationEmbedding.model_digest, func.count())
        .where(MemeProfileSituationEmbedding.image_id.in_(ids))
        .group_by(MemeProfileSituationEmbedding.model_name, MemeProfileSituationEmbedding.model_digest))
    return {
        "corpusImages": len(ids),
        "profiled": profiled,
        "taxonomyVersions": await counted(MemeProfile.taxonomy_version, MemeProfile.image_id),
        "promptVersions": await counted(MemeProfile.extractor_prompt_version, MemeProfile.image_id),
        "extractorModels": await counted(MemeProfile.extractor_model, MemeProfile.image_id),
        "embedded": embedded,
        "embeddingModels": {f"{name} {digest}": count for name, digest, count in embedding_rows.all()},
    }
