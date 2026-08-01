from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from Storage.models import ImageExtras


class ImageExtrasRepository:

    def __init__(self, session):
        self.session = session

    async def set_flagged(self, image_id, flagged: bool) -> None:
        stmt = (
            insert(ImageExtras)
            .values(image_id=image_id, flagged=flagged)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"flagged": flagged},
            )
        )
        await self.session.execute(stmt)

    async def get_flags_bulk(self, image_ids: list) -> dict:
        """Bulk-fetch flagged status for a set of image_ids in one query. Every id in
        image_ids is guaranteed a key in the result -- an id with no image_extras row
        at all (never flagged/unflagged) maps to False."""
        result = await self.session.execute(
            select(ImageExtras.image_id, ImageExtras.flagged)
            .where(ImageExtras.image_id.in_(image_ids))
        )
        flags = {image_id: bool(flagged) for image_id, flagged in result.all()}
        return {image_id: flags.get(image_id, False) for image_id in image_ids}
