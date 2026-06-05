from sqlalchemy.dialects.postgresql import insert

from Storage.models import ImageExtras


class ImageExtrasRepository:

    def __init__(self, session):
        self.session = session

    async def set_excluded(self, image_id, excluded: bool) -> None:
        stmt = (
            insert(ImageExtras)
            .values(image_id=image_id, exclude=excluded)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"exclude": excluded},
            )
        )
        await self.session.execute(stmt)