from sqlalchemy import delete

from Storage.models import ImageMetrics


class ImageMetricsRepository:

    def __init__(self, session):
        self.session = session

    async def overwrite_metrics(self, image, metrics):
        await self.session.execute(
            delete(ImageMetrics).where(
                ImageMetrics.image_id == image.id
            )
        )

        self.session.add(
            ImageMetrics(
                image_id=image.id,
                **metrics
            )
        )