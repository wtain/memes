from sqlalchemy import exists, func, select, text, true, false
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import (
    Concept, ConceptImage, ConceptImageSet,
    Embedding, Image, ImageExtras,
    ImageTag, OCRText, ImageDescription, ImageDescriptionFeedback, TmpImageClusters, BatchRun, TrendSource,
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
        # Per-image inventory/coverage counts are scoped to status == "active" so a
        # pending (not-yet-reviewed) or rejected image never inflates corpus-size or
        # corpus-completeness stats shown to users -- see
        # docs/superpowers/specs/2026-07-25-image-visibility-status-design.md.
        result = await self._session.execute(
            select(
                select(func.count()).select_from(Image)
                    .where(Image.status == "active")
                    .scalar_subquery().label("total_memes"),
                select(func.count(Embedding.image_id.distinct()))
                    .select_from(Embedding).join(Image, Image.id == Embedding.image_id)
                    .where(Image.status == "active")
                    .scalar_subquery().label("with_embeddings"),
                select(func.count(OCRText.image_id.distinct()))
                    .select_from(OCRText).join(Image, Image.id == OCRText.image_id)
                    .where(Image.status == "active")
                    .scalar_subquery().label("with_ocr"),
                select(func.count(ImageTag.image_id.distinct()))
                    .select_from(ImageTag).join(Image, Image.id == ImageTag.image_id)
                    .where(Image.status == "active")
                    .scalar_subquery().label("with_tags"),
                select(func.count()).select_from(Image)
                    .where(
                        Image.status == "active",
                        ~exists(select(ImageTag.image_id).where(ImageTag.image_id == Image.id)),
                    )
                    .scalar_subquery().label("without_tags"),
                select(func.count(ImageDescription.image_id.distinct()))
                    .select_from(ImageDescription).join(Image, Image.id == ImageDescription.image_id)
                    .where(Image.status == "active")
                    .scalar_subquery().label("with_descriptions"),
                select(func.count()).select_from(ImageExtras)
                    .where(ImageExtras.flagged == true())
                    .scalar_subquery().label("flagged"),
                select(func.count(TmpImageClusters.cluster_id.distinct()))
                    .scalar_subquery().label("duplicate_clusters"),
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
                    .select_from(ImageTag).join(Image, Image.id == ImageTag.image_id)
                    .where(ImageTag.source == "CONCEPT", Image.status == "active")
                    .scalar_subquery().label("with_concept_tags"),
                select(func.count(ImageTag.key.distinct()))
                    .scalar_subquery().label("tag_keys"),
                select(func.count()).select_from(
                    select(ImageTag.key, ImageTag.value).distinct().subquery()
                ).scalar_subquery().label("tag_values"),
                select(func.count()).select_from(BatchRun)
                    .where(BatchRun.kind == "trends")
                    .scalar_subquery().label("trends_runs"),
                select(func.count()).select_from(TrendSource)
                    .scalar_subquery().label("trend_sources"),
                select(func.count()).select_from(ImageDescriptionFeedback)
                    .where(ImageDescriptionFeedback.approved == true())
                    .scalar_subquery().label("descriptions_approved"),
                select(func.count()).select_from(ImageDescriptionFeedback)
                    .where(ImageDescriptionFeedback.approved == false())
                    .scalar_subquery().label("descriptions_rejected"),
                select(func.count()).select_from(ImageDescriptionFeedback)
                    .scalar_subquery().label("descriptions_feedback_total"),
            )
        )
        return result.one()
