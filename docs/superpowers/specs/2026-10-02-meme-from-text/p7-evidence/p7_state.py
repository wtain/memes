import asyncio, json
from pathlib import Path
from sqlalchemy import select, func
from evaluation.gold import load_corpus
from Storage.db import AsyncSessionLocal
from Storage.models import MemeProfile, MemeProfileSituationEmbedding
import uuid
async def main():
    ids = [uuid.UUID(i) for i in load_corpus(Path("evaluation/gold/corpus.json")).image_ids()]
    async with AsyncSessionLocal() as s:
        prof = (await s.execute(select(MemeProfile.taxonomy_version, MemeProfile.extractor_prompt_version, MemeProfile.extractor_model, func.count(), func.max(MemeProfile.updated_at)).where(MemeProfile.image_id.in_(ids)).group_by(MemeProfile.taxonomy_version, MemeProfile.extractor_prompt_version, MemeProfile.extractor_model))).all()
        emb = (await s.execute(select(MemeProfileSituationEmbedding.model_name, MemeProfileSituationEmbedding.model_digest, func.count(), func.max(MemeProfileSituationEmbedding.created_at)).where(MemeProfileSituationEmbedding.image_id.in_(ids)).group_by(MemeProfileSituationEmbedding.model_name, MemeProfileSituationEmbedding.model_digest))).all()
    print("profiles:", [(a, b, c, n, str(t)[:19]) for a, b, c, n, t in prof])
    print("embeddings:", [(a, b[:10], n, str(t)[:19]) for a, b, n, t in emb])
asyncio.run(main())
