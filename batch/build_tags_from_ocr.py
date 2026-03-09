import asyncio
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from batch.models.external import AsyncSessionLocal
from batch.models.external import ImageTag, OCRText, Image


# Band to tags
# tag: name -> value (OR values)

# synonyms? multiple tokens could lead to same concepts


rules = {
    "opeth": {
        "band": "opeth",
        "country": "sweden",
        "genre": "progressive melodic death metal",
        "lore": "sorrow",
        "person": "mikael akkerfeldt"
    },
    "ghost of perdition": "opeth",
    "mikael akkerfeldt": "opeth",
    "metallica": {
        "band": "metallica",
        "country": "usa",
        "genre": "thrash metal",
        "lore": "booze",
        "person": ["james hetfield", "lars ulrich", "kirk hammett", "robert trujilho"]
    },
    "hetfield": "metallica",
    "burzum": {
        "band": "burzum",
        "country": "norway",
        "genre": "black metal",
        "lore": ["satan", "homicide", "stabbed", "arson"],
        "person": ["varg vikernes", "euronymous", "dead"]
    },
    "varg": "burzum",
    "motorhead": {
        "band": "motorhead",
        "country": "great britain",
        "genre": "speed metal",
        "lore": "booze",
        "person": "lemmy kilmister"
    },
    "lemmy": "motorhead",
    "babymetal": {
        "band": "babymetal",
        "country": "japan",
        "genre": "kawai metal",
        "lore": "anime",
    },
    "baby metal": "babymetal",
    "nickelback": {
        "band": "nickelback",
        "country": "canada",
        "genre": "hard rock",
        "person": "chad kroger"
    },
    "slayer": {
        "band": "slayer",
        "country": "usa",
        "genre": "thrash metal",
        "lore": ["booze", "anti religion", "satan"],
        "person": ["kerry king", "jeff hanneman", "dave lombardo", "tom araya"]
    },
    "slipknot": {
        "band": "slipknot",
        "country": "usa",
        "genre": "nu metal",
        "person": ["corey taylor", "joey jordison"]
    },
    "deicide": {
        "band": "deicide",
        "country": "usa",
        "genre": "death metal",
        "lore": ["deicide", "blasphemy"],
        "person": "glen benton"
    },
    "lorna shore": {
        "band": "lorna shore",
        "country": "usa",
        "genre": "symphonic deathcore",
        "lore": ["snarling"],
        "person": "wil ramos"
    },
    "nirvana": {
        "band": "nirvana",
        "country": "usa",
        "genre": "grunge",
        "lore": ["suicide", "depression", "seattle"],
        "person": ["kurt kobain", "dave grohl", "kris novoselic"]
    },
    "oasis": {
        "band": "oasis",
        "country": "usa",
        "genre": "pop punk",
    },
    "six feet under": {
        "band": "six feet under",
        "country": "usa",
        "genre": "death metal",
        "lore": ["eee"],
        "person": "chris barnes"
    },
    "cannibal corpse": {
        "band": "cannibal corpse",
        "country": "usa",
        "genre": "death metal",
        "lore": ["mutiliation", "corpsegrinder"],
        "person": "george fisher"
    },
    "dying fetus": {
        "band": "dying fetus",
        "country": "usa",
        "genre": "death metal",
        "lore": ["antagonism"],
    },
    "judas priest": {
        "band": "judas priest",
        "country": "great britain",
        "genre": "speed metal",
        "lore": ["gays"],
        "person": ["rob halford"],
    },
    "system of a down": {
        "band": "system of a down",
        "country": "usa",
        "genre": "nu metal",
        "lore": ["oil", "antagonism", "war", "weed", "california"],
        "person": ["serj tankian", "daron malakian", "shavo odadjian", "johnm dolmayan"],
    },
    "ac/dc": {
        "band": "ac/dc",
        "country": "australia",
        "genre": "hard rock",
        "person": ["angus young"],
    },
    "wintersun": {
        "band": "wintersun",
        "country": "finland",
        "genre": "epic symphonic melodic death metal",
        "lore": ["album delay", "nature", "snow"],
        "person": ["jari maenpaa"],
    },
    "megadeth": {
        "band": "megadeth",
        "country": "usa",
        "genre": "thrash metal",
        "lore": ["antagonism"],
        "person": ["dave mustaine"],
    },
}

async def main():

    async with AsyncSessionLocal() as session:
        print("Deleting all tags...")
        await session.execute(
            delete(ImageTag)
        )
        await session.commit()
        print("Done")

        images_repo = ImagesRepository(session)

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")

        print("Running...")
        images_and_texts_results = await images_repo.get_images_and_texts()
        # rules_matched_for_image = defaultdict(set)
        image_tags = defaultdict(set)
        for filename, image_id, text, confidence in images_and_texts_results:
            if confidence < 0.4:
                continue
            for band_name in rules:
                # if band_name in rules_matched_for_image[filename]:
                #     continue

                if band_name in str.lower(text):

                    # rules_matched_for_image[filename].add(band_name)

                    while type(rules[band_name]) is str:
                        # print(f"Redirecting from {band_name} to {rules[band_name]}")
                        band_name = rules[band_name]

                    for tag_name, tag_value in rules[band_name].items():
                        if type(tag_value) is not list:
                            tag_value = [tag_value]
                        for value in tag_value:
                            dedup_key = f"{tag_name}:{value}"
                            if dedup_key not in image_tags[image_id]:
                                image_tags[image_id].add(dedup_key)
                                session.add(ImageTag(key=tag_name,
                                                     value=value,
                                                     source="OCR",
                                                     image_id=image_id))


        print("Committing...")
        await session.commit()
        print("Done")

class ImagesRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.ocr = aliased(OCRText)
        self.session = session

    async def get_images_and_texts(self):
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


    async def get_total_images(self):
        total_images = (await self.session.execute(
            select(count(Image.id))
        )).scalar_one()
        return total_images


if __name__ == "__main__":
    asyncio.run(main())