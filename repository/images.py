
from sqlalchemy import select, delete, update
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from Storage.models import OCRText, Image, ImageDescription, ImageTag, OCRLemma


class ImagesRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.ocr = aliased(OCRText)
        self.description = aliased(ImageDescription)
        self.session = session

    async def get_images_and_ocr_texts(self):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.lang_score
            ).join(
                self.ocr, self.ocr.image_id == self.img.id
            )
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_without_tags(self, source: str):
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
            .where(self.img.id.not_in(already_tagged))
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_with_language(self):
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
            )
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_without_tags_with_language(self, source: str):
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
            .where(self.img.id.not_in(already_tagged))
        )
        result = await self.session.execute(query)
        return result.fetchall()


    async def get_images_and_ocr_texts_without_lemmas_with_language(self):
        already_indexed = (
            select(OCRLemma.image_id)
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
            .where(self.img.id.not_in(already_indexed))
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_descriptions(self):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.description.text
            ).join(
                self.description, self.description.image_id == self.img.id
            )
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_descriptions_without_tags(self, source: str):
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
                self.description.text
            )
            .join(self.description, self.description.image_id == self.img.id)
            .where(self.img.id.not_in(already_tagged))
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_all_images_with_hash(self):
        query = select(Image.id, Image.filename, Image.content_hash, Image.created_at)
        result = await self.session.execute(query)
        return result.fetchall()

    async def update_content_hash(self, image_id, content_hash: str) -> None:
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(content_hash=content_hash)
        )

    async def get_all_images(self):
        query = (
            select(
                Image.filename,
                Image.id,
            )
        )
        images = await self.session.execute(query)
        return images


    async def iterate_images(self):
        stmt = (
            select(Image.filename, Image.id)
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


    async def get_total_images(self):
        total_images = (await self.session.execute(
            select(count(Image.id))
        )).scalar_one()
        return total_images

    async def find_image_by_filename(
        self,
        filename: str,
    ) -> Image | None:
        result = await self.session.execute(
            select(Image).where(Image.filename == filename)
        )
        return result.scalar_one_or_none()

    async def register_image(self, file):
        image = Image(
            filename=file
        )
        self.session.add(image)
        await self.session.flush()  # image.id available
        return image
