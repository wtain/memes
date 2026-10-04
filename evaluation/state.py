"""A snapshot of the profile state of the corpus, recorded in every baseline (P6 spec, decision 8)."""
import uuid
from typing import Sequence

from sqlalchemy import func, select

from evaluation.gold import Corpus
from Storage.models import Image, MemeProfile, MemeProfileSituationEmbedding


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


async def collect_corpus_drift(session, corpus: Corpus) -> dict:
    """How far the database has moved from the frozen corpus (read-only): ids gone, no longer active, hash changed."""
    stored = {image.image_id: image.content_hash for image in corpus.images}
    rows = await session.execute(
        select(Image.id, Image.content_hash, Image.status).where(Image.id.in_([uuid.UUID(i) for i in stored])))
    found = {str(image_id): (content_hash, status) for image_id, content_hash, status in rows.all()}
    missing = sum(1 for image_id in stored if image_id not in found)
    inactive = sum(1 for _, status in found.values() if status != "active")
    changed = sum(1 for image_id, (content_hash, _) in found.items()
                  if stored[image_id] is not None and stored[image_id] != content_hash)
    return {"missing": missing, "inactive": inactive, "hashChanged": changed}


def embedding_digest_warnings(state_embedding_models: dict, model_name: str, current_digest: str) -> list[str]:
    """Warnings when stored situation embeddings for `model_name` were made with a different model digest."""
    warnings = []
    for key, count in sorted(state_embedding_models.items()):
        name, _, digest = key.partition(" ")
        if name == model_name and digest != current_digest:
            warnings.append(f"{count} situation embeddings were made with {model_name} digest {digest!r}, but the "
                            f"current digest is {current_digest!r}; re-embed with build_meme_profile_embeddings")
    return warnings
