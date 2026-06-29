
from sqlalchemy import select, delete, update
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from Storage.models import OCRText, Image, OllamaDescription, ImageTag


class ImagesRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.ocr = aliased(OCRText)
        self.description = aliased(OllamaDescription)
        self.session = session

    async def get_images_and_ocr_texts(self):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence
            ).join(
                self.ocr, self.ocr.image_id == self.img.id
            )
        )
        images_and_texts_results = await self.session.execute(query)
        return images_and_texts_results

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
                self.ocr.confidence
            )
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(self.img.id.not_in(already_tagged))
        )
        return await self.session.execute(query)

    async def get_images_and_ollama_descriptions(self):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.description.text
            ).join(
                self.description, self.description.image_id == self.img.id
            )
        )
        images_and_texts_results = await self.session.execute(query)
        return images_and_texts_results

    async def get_images_and_ollama_descriptions_without_tags(self, source: str):
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
        return await self.session.execute(query)

    async def get_all_images_without_description(self):
        has_description = (
            select(OllamaDescription.image_id)
            .distinct()
            .scalar_subquery()
        )
        query = select(Image.filename, Image.id).where(Image.id.not_in(has_description))
        return await self.session.execute(query)


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
        # await self.session.commit()  # not optimal
        return image
