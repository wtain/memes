import asyncio
import re
from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.functions import count

from batch.models.external import AsyncSessionLocal
from batch.models.external import ImageTag, Image, OllamaDescription


# Band to tags
# tag: name -> value (OR values)

# synonyms? multiple tokens could lead to same concepts

# Copypasted from OCR batch. todo: externalise
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
    "chad kroeger": "nickelback",
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
        "person": "will ramos"
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
        "person": ["serj tankian", "daron malakian", "shavo odadjian", "john dolmayan"],
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
    "taylor swift": {
        "band": "taylor swift",
        "country": "usa",
        "genre": "pop",
        "lore": ["relationship", "love"],
        "person": ["taylor swift"],
    },
    "black sabbath": {
        "band": "black sabbath",
        "country": "great britain",
        "genre": "heavy metal",
        "lore": ["witches", "antagonism"],
        "person": ["ozzy osbourne", "geeze butler", "tony iommi"],
    },
    "iron maiden": {
        "band": "iron maiden",
        "country": "great britain",
        "genre": "heavy metal",
        "lore": ["history", "war"],
        "person": ["bruce dickinson", "dave murray"],
    },
    "steel panther": {
        "band": "steel panther",
        "country": "usa",
        "genre": "glam metal",
        "lore": ["parties", "booze"],
    },
    "alice in chains": {
        "band": "alice in chains",
        "country": "usa",
        "genre": "grunge",
    },
    "soundgarden": {
        "band": "soundgarden",
        "country": "usa",
        "genre": "grunge",
        "person": "chris cornell",
    },
    "mayhem": {
        "band": "mayhem",
        "country": "norway",
        "genre": "black metal",
        "lore": ["satan", "homicide"],
        "person": ["euronymous"]
    },
    "euronymous": "mayhem",
    "arch enemy": {
        "band": "arch enemy",
        "country": "sweden",
        "genre": "melodic death metal",
        "lore": ["antagonism", "society"],
        "person": ["michael ammott", "angela gossow", "alissa white-gluz"]
    },
    "korn": {
        "band": "korn",
        "country": "usa",
        "genre": "nu metal",
        "lore": ["antagonism", "society"],
        "person": ["jonathan davis"]
    },
    "limp bizkit": {
        "band": "limp bizkit",
        "country": "usa",
        "genre": ["nu metal", "rap metal"],
        "lore": ["antagonism", "society"],
        "person": ["fred durst"]
    },
    "linkin park": {
        "band": "linkin park",
        "country": "usa",
        "genre": ["nu metal", "rap metal"],
        "lore": ["society"],
        "person": ["michael shinoda", "emily armstrong", "chester bennington"]
    },
    "sepultura": {
        "band": "sepultura",
        "country": "brazil",
        "genre": ["groove metal"],
        "lore": ["society"],
        "person": ["max cavaliera", "igor cavaliera"]
    },
    "pantera": {
        "band": "pantera",
        "country": "usa",
        "genre": ["glam metal", "groove metal"],
        "lore": ["society"],
        "person": ["dimebag darell", "phil anselmo"]
    },
    "gojira": {
        "band": "gojira",
        "country": "france",
        "genre": ["grove metal", "progressive metal"],
        "lore": ["society", "whales"],
        "person": ["joe duplantier"]
    },
    "meshuggah": {
        "band": "meshuggah",
        "country": "sweden",
        "genre": ["mat metal"],
        "lore": ["society"],
    },
    "black metal": {
        "genre": "black metal",
        "lore": "satan",
    },
    "death metal": {
        "genre": "death metal",
    },

    # added
    "cat": {
        "lore": "animals",
        "animal": "cat",
    },

    "serj tankian": {
        "lore": "music",
        "person": "serj tankian",
        "genre": "numetal",
        "band": "system of a down",
    },

    "jesus": {
        "lore": "religion",
        "character": "jesus christ",
    },

    "skeletor": {
        "lore": "cartoons",
        "character": "skeletor",
    },

    "frog": {
        "lore": "animals",
        "animal": "frog",
    },

    "toad": {
        "lore": "animals",
        "animal": "toad",
    },

    "dog": {
        "lore": "animals",
        "animal": "dog",
    },

    "metalocalypse": {
        "lore": "music",
        "genre": ["metal", "death metal", "melodic death metal"],
    },

    "kerry king": {
        "lore": "music",
        "genre": ["metal", "thrash metal"],
        "band": "slayer",
    },

    "the cure": {
        "genre": ["proto-punk", "rock"],
        "band": "the cure",
    },
    "beatles": {
        "genre": "rock",
        "band": "beatles",
    },
    "rolling stones": {
        "genre": "rock",
        "band": "rolling stones",
    },
    "the police": {
        "genre": "soft rock",
        "band": "the police",
    },
    "the doors": {
        "genre": "rock",
        "band": "the doors",
    },
    "def leppard": {
        "genre": ["rock", "glam rock"],
        "band": "def leppard",
    },
    "avril lavigne": {
        "genre": ["punk rock", "pop punk"],
        "band": "avril lavigne",
        "person": "avril lavigne",
    },
    "static-x": {
        "genre": ["alternative", "nu metal"],
        "band": "static-x",
    },
    "deftones": {
        "genre": ["alternative", "nu metal"],
        "band": "deftones",
    },
    "mudvayne": {
        "genre": ["alternative", "nu metal"],
        "band": "mudvayne",
    },
    "anthrax": {
        "genre": "thrash metal",
        "band": "anthrax",
    },
    "lamb of god": {
        "genre": "groove metal",
        "band": "lamb of god",
    },
    "infant annihilator": {
        "genre": ["death metal", "deathcore"],
        "band": "infant annihilator",
    },
    "van halen": {
        "genre": "glam metal",
        "band": "van halen",
    },
    "yoda": {
        "lore": "star wars",
        "person": "master yoda",
    },
    "the office": {
        "lore": "the office",
    },
    "borat": {
        "person": "borat",
    },
    "kiss band": {
        "genre": "glam metal",
        "band": "kiss",
    },
    "iggy pop": {
        "genre": "rock",
        "band": "iggy pop",
    },
    "tim lambesis": {
        "person": "tim lambesis",
        "band": "as i lay dying",
        "genre": "metalcore",
    },
    "red hot chili peppers": {
        "band": "red hot chili peppers",
        "genre": ["progressive rock", "funk metal"],
    },
    "anthony kiedis": {
        "band": "red hot chili peppers",
        "person": "anthony kiedis",
    },
    "autism": {
        "lore": "autism",
    },
    "obituary": {
        "genre": "death metal",
        "band": "obituary",
    },
    "canibal corpse": "cannibal corpse",
    "agalloch": {
        "genre": ["fold metal", "black metal"],
        "band": "agalloch",
    },
    "children of bodom": {
        "genre": "melodic death metal",
        "band": "children of bodom",
        "country": "finland",
    },

    "dream theater": {
        "genre": "progressive metal",
        "band": "dream theater",
        "country": "usa",
    },
    "progressive metal": {
        "genre": "progressive metal",
    },
    "gutalax": {
        "genre": ["grindcore", "coprogrind"],
        "band": "gutalax",
    },
    "shark": {
        "lore": "animals",
        "animal": "shark",
    },
    "sesame street": {
        "lore": "sesame street",
    },
    "zz top": {
        "genre": ["southern rock", "hard rock"],
        "band": "zz top",
    },
    "barbie girl": {
        "band": "aqua",
        "genre": "pop",
        "song": "barbie girl",
        "lore": "barbie",
    },
    "powerwolf": {
        "genre": "power metal",
        "lore": "religion",
        "band": "powerwolf",
    },
    "sabaton": {
        "genre": "power metal",
        "lore": "war",
        "band": "sabaton",
    },
    "twisted sister": {
        "genre": "glam metal",
        "band": "twisted suster",
    },
    "lord of the rings": {
        "lore": "lord of the rings",
    },
    "beavis": {
        "lore": "beavis and butthead",
        "person": "beavis",
    },
    "pearl jam": {
        "band": "pearl jam",
        "country": "usa",
        "genre": "grunge",
    },
    "bring me the horizon": {
        "band": "bring me the horizon",
        "country": "usa",
        "genre": "metalcore",
    },
    "foo fighters": {
        "band": "foo fighters",
        "country": "usa",
        "genre": ["hard rock", "grunge"],
    },
}

async def main():

    async with AsyncSessionLocal() as session:
        print("Deleting all Ollama tags...")
        await session.execute(
            delete(
                ImageTag
            )
            .where(
                ImageTag.source == "Ollama"
            )
        )
        await session.commit()
        print("Done")

        images_repo = ImagesRepository(session)

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")

        print("Running...")
        images_and_texts_results = await images_repo.get_images_and_texts()
        image_tags = defaultdict(set)
        for filename, image_id, text in images_and_texts_results:
            for token in rules:
                # if token in str.lower(text):
                if re.search(rf"\b{re.escape(token)}\b", text, re.IGNORECASE):

                    while type(rules[token]) is str:
                        token = rules[token]

                    for tag_name, tag_value in rules[token].items():
                        if type(tag_value) is not list:
                            tag_value = [tag_value]
                        for value in tag_value:
                            dedup_key = f"{tag_name}:{value}"
                            if dedup_key not in image_tags[image_id]:
                                image_tags[image_id].add(dedup_key)
                                session.add(ImageTag(key=tag_name,
                                                     value=value,
                                                     source="Ollama",
                                                     image_id=image_id))


        print("Committing...")
        await session.commit()
        print("Done")

        print(f"Total images tagged: {len(image_tags.keys())}")

class ImagesRepository:

    def __init__(self, session):
        self.img = aliased(Image)
        self.description = aliased(OllamaDescription)
        self.session = session

    async def get_images_and_texts(self):
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


    async def get_total_images(self):
        total_images = (await self.session.execute(
            select(count(Image.id))
        )).scalar_one()
        return total_images


if __name__ == "__main__":
    asyncio.run(main())