import asyncio
import os

from sqlalchemy import select, exists
from sqlalchemy.sql.functions import count

from Storage.db import AsyncSessionLocal, init_db
from embeddings import EmbeddingsDetector
from Storage.models import Image, OCRText, Embedding


async def main():

    # do once
    await init_db()

    async with AsyncSessionLocal() as session:

        total_images = (await session.execute(
            select(count(Image.id))
        )).scalar_one()

        # stmt = (
        #     select(Image.filename)
        #     .where(
        #         ~exists().where(OCRText.image_id == Image.id)
        #     )
        # )
        # result = await session.execute(stmt)
        # no_ocr_count = len([fn for (fn,) in result])

        # no_ocr_count = (await session.execute(
        #     select(count(Image.id))
        #     .where(
        #         ~exists().where(OCRText.image_id == Image.id)
        #     )
        # )).scalar_one()


        stmt = (
            select(Image.filename, Image.id)
        )
        result = await session.execute(stmt)

        detector = EmbeddingsDetector([
            "Death metal band logo",
            "Xavleg logo",
            "Saint Anger, Metallica, Lars Ulrich",
            "James Hetfield from Metallica",
            "Lars Ulrich from Metallica",
            "cat",
            "dog",
            "girl, woman",
            "car",
            "Avril Lavigne, punk",
            "Punk",
            "meme",
            "DSBM, depressive suicidal black metal",
            "pantera, groove metal, rednecks, phil anselmo",
            "emo, punk, hardcore",
            "George Fisher, Corpsegrinder, Cannibal Corpse",
            "Simpsons, Homer Simpson",
            "Dave Mustaine, Megadeth",
            "Camo shirt",
            "Nicolas Cage",
            "Arch Enemy, Angela Gossow, Alissa White-Gluz, Metal, Blue hair",
            "Mosh pit, gig",
            "Family Guy",
            "Ozzy, Ozzy Osbourne, Black Sabbath, bat",
            "Glen Benton, Deicide",
            "Futurama",
            "Radiohead, head, Tom Yorke",
            "Beavis and Butthead, cartoon",
            "Corpsepaint",
            "Lemmy Kilmister from Motorhead",
            "Peter Steele, Gothic Rock, Doom Metal, Type-O-Negative",
            "Mona Lisa",
            "Slayer, Tom Araya, Jeff Hanneman",
            "Anime",
            "Padme, Star Wars",
            "Beatles, Abbey Road",
            "Parks and Recreation",
            "Breaking bad, Walter White, Jesse Pinkman",
            "Iron Maiden, Eddie",
            "Tank, War, Bolt Thrower",
            "Grim Reaper, Skull, scelet, scythe",
            "The Cure, Edward scissor-hands",
            "campfire, camping, fire, woman, girl",
            "Pavarotti",
            "Brainrot, shark, tralalero tralala",
            "Davie Jones",
            "BabyMetal",
            "Children of Bodom",
            "Spotify wrapped",
            "Norther",
            "Kalmah",
            "RHCP, Red Hot Chili Peppers",
            "California",
            "Silicon Valley TV Show",
            "Dinosaurs",
            "Sesame Street",
        ])
        # rule: text -> tags (text embeddings match image embeddings, threshold => tags)
        base_path = os.path.abspath("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\")
        threshold = 30.0
        non_matched_count_first = 100

        images_matched = 0
        images_not_matched = []
        for (filename, image_id,) in result:
            path = os.path.join(base_path, filename)
            try:
                results = detector.detect_embeddings(path)
                probable_tokens = [(token, results[token]) for token in results if results[token] > threshold]
                if not probable_tokens:
                    images_not_matched.append(filename)
                    continue
                images_matched += 1
                print(filename)
                for token, probability in probable_tokens:
                    print(f'-> {token} ({probability})')
                    # todo: don't store duplicates? remove duplicates
                    session.add(Embedding(image_id=image_id, text=token, confidence=probability))
            except FileNotFoundError:
                pass

        # todo: batch database queries
        await session.commit()

        print('-' * 20)
        print(f"Total images: {total_images}")
        # print(f"No OCR: {no_ocr_count}")
        print(f"Images covered by rules: {images_matched}")
        print(f"Images not matched (first {non_matched_count_first}): {images_not_matched[:non_matched_count_first]}")

if __name__ == "__main__":
    asyncio.run(main())