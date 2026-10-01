import uuid

from sqlalchemy import delete, exists, select

from Storage.models import ImageDescription, ImageDescriptionFeedback


def description_not_rejected(description_id_col):
    """True when the description has no feedback row with approved = FALSE.

    The one definition of "rejected" (no feedback row = unreviewed = included). Takes the
    description id column so it works against an aliased ImageDescription too; the EXISTS
    correlates to whichever outer query contains that column.
    """
    return ~exists().where(
        ImageDescriptionFeedback.image_description_id == description_id_col,
        ImageDescriptionFeedback.approved.is_(False),
    )


class ImageDescriptionsRepository:

    def __init__(self, session):
        self.session = session

    async def delete_all(self) -> None:
        await self.session.execute(delete(ImageDescription))

    def save(self, image_id, prompt_key: str, model_used: str, text: str) -> None:
        self.session.add(ImageDescription(
            image_id=image_id, prompt_key=prompt_key, model_used=model_used, text=text,
        ))

    async def get_all_texts(self) -> list[str]:
        result = await self.session.execute(select(ImageDescription.text))
        return result.scalars().all()

    async def get_image_ids_with_prompt(self, prompt_key: str) -> set[uuid.UUID]:
        result = await self.session.execute(
            select(ImageDescription.image_id)
            .where(ImageDescription.prompt_key == prompt_key)
            .distinct()
        )
        return set(result.scalars().all())
