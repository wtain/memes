import uuid
from datetime import datetime

from sqlalchemy import delete, select

from Storage.models import ImageProcessingStatus


class ImageProcessingStatusRepository:

    def __init__(self, session, pipeline):
        self.session = session
        self.pipeline = pipeline

    async def mark_started(self, image):
        status = await self.get_image_status(image.id)
        if status is None:
            status = ImageProcessingStatus(# image=image,
                                           image_id=image.id,
                                           pipeline=self.pipeline, status="processing",
                                           started_at=datetime.utcnow())
        else:
            status.status="processing"
        self.session.add(
            status
        )
        await self.session.commit() # image might not yet available here (id only - after flush)

    async def get_image_status(self, image_id):
        existing = await self.session.get(
            ImageProcessingStatus,
            {"image_id": image_id, "pipeline": self.pipeline}
        )
        return existing

    async def mark_done(self, image):
        status = await self.session.get(
            ImageProcessingStatus,
            {"image_id": image.id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image=image, pipeline=self.pipeline)
            self.session.add(status)
        status.status = "done"
        status.finished_at = datetime.utcnow()

    async def mark_failed(self, image, error):
        status = await self.session.get(
            ImageProcessingStatus,
            {"image_id": image.id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image=image, pipeline=self.pipeline)
        status.status = "failed"
        status.error_message = str(error)
        status.finished_at = datetime.utcnow()
        await self.session.commit()

    async def should_process(self, image_id: str) -> bool:
        existing = await self.get_image_status(image_id)

        if existing:
            if existing.status == "done":
                return False  # already processed
            if existing.status == "processing":
                return True  # treat as interrupted, retry
        return True

    async def record_failure(self, image_id, error: str) -> None:
        """No commit — caller controls commit timing via its own batch committer."""
        status = await self.session.get(
            ImageProcessingStatus, {"image_id": image_id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image_id=image_id, pipeline=self.pipeline)
            self.session.add(status)
        status.status = "failed"
        status.error_message = error
        status.finished_at = datetime.utcnow()

    async def mark_done_by_id(self, image_id) -> None:
        """No commit — caller controls commit timing via its own batch committer."""
        status = await self.session.get(
            ImageProcessingStatus, {"image_id": image_id, "pipeline": self.pipeline}
        )
        if status is None:
            status = ImageProcessingStatus(image_id=image_id, pipeline=self.pipeline)
            self.session.add(status)
        status.status = "done"
        status.finished_at = datetime.utcnow()

    async def get_image_ids_with_status(self, status: str) -> set[uuid.UUID]:
        result = await self.session.execute(
            select(ImageProcessingStatus.image_id)
            .where(ImageProcessingStatus.pipeline == self.pipeline, ImageProcessingStatus.status == status)
        )
        return set(result.scalars().all())

    async def delete_all(self) -> None:
        """No commit — same convention as record_failure."""
        await self.session.execute(
            delete(ImageProcessingStatus).where(ImageProcessingStatus.pipeline == self.pipeline)
        )
