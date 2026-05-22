
from sqlalchemy import select, delete
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from Storage.models import Embedding, OCRText, Image, OllamaDescription


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

    # Copy from backend
    async def get_similar(self, image_id: str, embedding, limit: int = 10):
        img = aliased(Image)
        embed = aliased(Embedding)
        result = await self.session.execute(
            select(embed.image_id, embed.embedding.cosine_distance(embedding), img.filename)
            .join(img, img.id == embed.image_id)
            .filter(embed.image_id != image_id)
            .order_by(embed.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        return result.all()