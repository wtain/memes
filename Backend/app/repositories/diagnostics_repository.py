from sqlalchemy import func, select, text, true
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, FeedSource, Image, ImageExtras,
    ImageTag, OCRText, OllamaDescription, TrendsRun,
)


class DiagnosticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def check_database(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    async def get_statistics(self):
        result = await self._session.execute(
            select(
                select(func.count()).select_from(Image)
                    .scalar_subquery().label("total_memes"),
                select(func.count(Embedding.image_id.distinct()))
                    .scalar_subquery().label("with_embeddings"),
                select(func.count(OCRText.image_id.distinct()))
                    .scalar_subquery().label("with_ocr"),
                select(func.count(ImageTag.image_id.distinct()))
                    .scalar_subquery().label("with_tags"),
                select(func.count()).select_from(Image)
                    .where(~Image.id.in_(select(ImageTag.image_id.distinct())))
                    .scalar_subquery().label("without_tags"),
                select(func.count(OllamaDescription.image_id.distinct()))
                    .scalar_subquery().label("with_descriptions"),
                select(func.count()).select_from(ImageExtras)
                    .where(ImageExtras.exclude == true())
                    .scalar_subquery().label("excluded"),
                select(func.count()).select_from(OCRText)
                    .scalar_subquery().label("ocr_texts"),
                select(func.count()).select_from(ImageTag)
                    .scalar_subquery().label("tags"),
                select(func.count()).select_from(Concept)
                    .scalar_subquery().label("concepts"),
                select(func.count()).select_from(ConceptImageSet)
                    .scalar_subquery().label("concept_image_sets"),
                select(func.count()).select_from(ConceptImage)
                    .scalar_subquery().label("concept_images"),
                select(func.count(ImageTag.image_id.distinct()))
                    .where(ImageTag.source == "CONCEPT")
                    .scalar_subquery().label("with_concept_tags"),
                select(func.count(ImageTag.key.distinct()))
                    .scalar_subquery().label("tag_keys"),
                select(func.count()).select_from(
                    select(ImageTag.key, ImageTag.value).distinct().subquery()
                ).scalar_subquery().label("tag_values"),
                select(func.count()).select_from(TrendsRun)
                    .scalar_subquery().label("trends_runs"),
                select(func.count()).select_from(FeedSource)
                    .scalar_subquery().label("feed_sources"),
            )
        )
        return result.one()
