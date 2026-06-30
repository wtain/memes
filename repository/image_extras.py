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
