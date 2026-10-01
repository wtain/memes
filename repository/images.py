
from sqlalchemy import select, delete, update, or_, func
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from Storage.models import OCRText, Image, ImageDescription, ImageTag, ImageProcessingStatus, DescriptionNote

OCR_LEMMAS_PIPELINE = "ocr_lemmas"


class ImagesRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.ocr = aliased(OCRText)
        self.description = aliased(ImageDescription)
        self.session = session

    async def get_images_and_ocr_texts(self, status: str = "active"):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.lang_score
            ).join(
                self.ocr, self.ocr.image_id == self.img.id
            ).where(self.img.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_without_tags(self, source: str, status: str = "active"):
        already_tagged = (
            select(ImageTag.image_id)
            .where(ImageTag.source == source)
            .distinct()
            .scalar_subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.lang_score
            )
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(self.img.id.not_in(already_tagged), self.img.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_with_language(self, status: str = "active"):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.language,
                self.ocr.lang_score,
            ).join(
                self.ocr, self.ocr.image_id == self.img.id
            ).where(self.img.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_without_tags_with_language(self, source: str, status: str = "active"):
        already_tagged = (
            select(ImageTag.image_id)
            .where(ImageTag.source == source)
            .distinct()
            .scalar_subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.language,
                self.ocr.lang_score,
            )
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(self.img.id.not_in(already_tagged), self.img.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()


    async def get_images_and_ocr_texts_without_lemmas_with_language(self, status: str = "active"):
        already_indexed = (
            select(ImageProcessingStatus.image_id)
            .where(
                ImageProcessingStatus.pipeline == OCR_LEMMAS_PIPELINE,
                ImageProcessingStatus.status == "done",
            )
            .scalar_subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.language,
                self.ocr.lang_score,
            )
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(self.img.id.not_in(already_indexed), self.img.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_descriptions(self, status: str = "active"):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.description.text
            ).join(
                self.description, self.description.image_id == self.img.id
            ).where(self.img.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_descriptions_needing_tags(self, source: str, status: str = "active"):
        """Every description of each image whose `source` tags are missing or stale.

        Stale means the image's newest description is newer than its newest `source` tag, i.e.
        it was (re-)described after it was last tagged. All of a selected image's descriptions
        are returned, not only the new ones, because the caller rewrites the image's tags from
        the full description set. An image whose descriptions yield no tags has no tag to
        compare against, so it is selected on every call; that is harmless (re-tagging is
        idempotent) but means "selected" does not imply "changed".
        """
        latest_description = (
            select(
                ImageDescription.image_id,
                func.max(ImageDescription.created_at).label("latest"),
            )
            .group_by(ImageDescription.image_id)
            .subquery()
        )
        latest_tag = (
            select(ImageTag.image_id, func.max(ImageTag.created_at).label("latest"))
            .where(ImageTag.source == source)
            .group_by(ImageTag.image_id)
            .subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.description.text
            )
            .join(self.description, self.description.image_id == self.img.id)
            .join(latest_description, latest_description.c.image_id == self.img.id)
            .outerjoin(latest_tag, latest_tag.c.image_id == self.img.id)
            .where(
                self.img.status == status,
                or_(
                    latest_tag.c.latest.is_(None),
                    latest_description.c.latest > latest_tag.c.latest,
                ),
            )
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_notes_needing_tags(self, source: str, status: str = "active"):
        """(image_id, text) of notes whose `source` tags are missing or stale (note edited after
        the image's newest `source` tag). A note that yields no tags has nothing to compare
        against, so it is re-selected every call: harmless, tagging is idempotent."""
        latest_tag = (
            select(ImageTag.image_id, func.max(ImageTag.created_at).label("latest"))
            .where(ImageTag.source == source)
            .group_by(ImageTag.image_id)
            .subquery()
        )
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .join(self.img, self.img.id == DescriptionNote.image_id)
            .outerjoin(latest_tag, latest_tag.c.image_id == DescriptionNote.image_id)
            .where(
                self.img.status == status,
                or_(latest_tag.c.latest.is_(None), DescriptionNote.updated_at > latest_tag.c.latest),
            )
        )
        return result.all()

    async def get_all_notes(self, status: str = "active"):
        result = await self.session.execute(
            select(DescriptionNote.image_id, DescriptionNote.text)
            .join(self.img, self.img.id == DescriptionNote.image_id)
            .where(self.img.status == status)
        )
        return result.all()

    async def get_all_images_with_hash(self, status: str = "active"):
        query = (
            select(Image.id, Image.filename, Image.content_hash, Image.created_at)
            .where(Image.status == status)
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def update_content_hash(self, image_id, content_hash: str) -> None:
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(content_hash=content_hash)
        )

    async def update_filename_and_hash(self, image_id, filename: str, content_hash: str | None = None) -> None:
        values = {"filename": filename}
        if content_hash is not None:
            values["content_hash"] = content_hash
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(**values)
        )

    async def update_dimensions(self, image_id, width: int, height: int) -> None:
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(width=width, height=height)
        )

    async def get_all_images(self, status: str = "active"):
        query = (
            select(
                Image.filename,
                Image.id,
            )
            .where(Image.status == status)
        )
        images = await self.session.execute(query)
        return images


    async def iterate_images(self, status: str = "active"):
        stmt = (
            select(Image.filename, Image.id)
            .where(Image.status == status)
        )
        result = await self.session.execute(stmt)
        for (filename, image_id,) in result:
            yield filename, image_id


    async def delete_images(self, ids):
        delete_query = (
            delete(
                Image
            )
            .where(
                Image.id.in_(ids)
            )
        )

        print("Deleting...")
        await self.session.execute(delete_query)
        print("Committing...")
        await self.session.commit()
        print("DONE")


    async def get_total_images(self, status: str = "active"):
        total_images = (await self.session.execute(
            select(count(Image.id)).where(Image.status == status)
        )).scalar_one()
        return total_images

    async def find_image_by_filename(
        self,
        filename: str,
    ) -> Image | None:
        # Deliberately not status-filtered -- callers (e.g. extract_text_from_memes.py's
        # registration/skip check) need to find a matching row regardless of pending/active/
        # rejected, then decide what to do based on its status themselves.
        result = await self.session.execute(
            select(Image).where(Image.filename == filename)
        )
        return result.scalar_one_or_none()

    async def register_image(self, file, status: str = "active", content_hash: str | None = None,
                              ingestion_batch_id=None):
        image = Image(
            filename=file,
            status=status,
            content_hash=content_hash,
            ingestion_batch_id=ingestion_batch_id,
        )
        self.session.add(image)
        await self.session.flush()  # image.id available
        return image
