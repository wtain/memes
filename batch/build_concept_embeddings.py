import asyncio
import math
import os
import pathlib
from itertools import product

import numpy as np
import open_clip
import torch
from sqlalchemy import delete

from batch.embeddingutils.image import load_image, embed_image
from batch.models.external import AsyncSessionLocal
from batch.models.external import ImageTag, OCRText, Embedding, Concept # , Image, Embedding


# concept name -> list of texts
# later we add images
concepts = {
    "Metallica": [
        "Big four thrash metal band",
        "Metal band authored songs: Master Of Puppets, Enter Sandman, Saint Anger, Creeping Death, The Unforgiven",
        "Metal band consists of James Hetfield, Lars Ulrich, Kirk Hammett and Robert Trujilho",
        "Lars Ulrich is allegedly a bad drummer"
    ],
    "Wintersun": [
        "metal band performing live",
        "epic stage lighting at metal concert",
        "long haired guitarist performing",
        "Metal Band constantly delaying album release"
    ],
    "Corpsepaint": [
        "people drawing scary faces with a black paint",
        "black metal corpsepaint makeup",
        "norwegian black metal face paint",
        "metal musician wearing corpsepaint",
        "white black corpsepaint face",
    ],
    "Glam metal": [
        "Male musicians dress in color clothes",
        "Long-haired guys look like girls",
    ],
    "Goth girl": [
        "girls with black hair and smoky eyes wearing fishnets",
    ],
    "Simpsons": [
        "a cartoon TV show",
        "all characters skin is yellow",
    ],
    "Dethklok": [
        "a cartoon tv show about a death metal band",
        "Nathan Explosion is a cartoon version of George Fisher aka Corpsegrinder"
    ],
    "Valentine card": [
        "a love letter with some warm wishes"
    ],
    "Black metal": [
        "a metal music subgenre with dark aesthetics",
        "musicians use corpsepaint",
        "band logos are usually extremely unreadable"
    ],
    "Heavy metal": [
        "musicians usually have long hair",
        "guitar-centric music",
        "musicians usually look eccentric",
        "Is countered by hard rock music"
    ],
    "Lord of the rings": [
        "A fantasy saga about different nations like hobbits, elves, orcs and humans",
        "There are multiple rings, and there are good and evil forces",
    ],
    "Black hole": [
        "a huge object in the outer space having extreme mass and thus extreme gravity"
    ],
    "Meshuggah face": [
        "an angry face with lower tooth inclined in forward direction"
    ],
    "emotion:surprised": [
        "surprised face",
        "a guy looks puzzled",
        "someone is confused"
    ],
    "emotion:angry": [
        "angry face",
        "bare teeth",
    ],
}

templates = [
    "a photo of {}",
    "a photo of a {}",
    "a picture of {}",
    "a picture of a {}",
    "a close-up photo of {}",
    "a concert photo of {}",
]

image_concepts = [
    "corpsepaint-black-metal",
    "extreme-metal-band-logo",
    "family-guy-cartoon-tv-show",
    "glam-metal-band",
    "goth-girl",
    "kiss-band",
    "lemmy-kilmister",
    "metallica",
    "simpsons-cartoon-tv-show",
    "doge-meme",
    "drake-meme",
    "distracted-boyfriend-meme",
    "change-my-mind-meme",
    "skeletor-until-we-meet-again-meme",
    "two-buttons-meme",
    "two-paths-meme",
    "couple-in-a-car-meme",
    "meme-pepe",
    "meme-slipknot",
    "meme-metalocalypse",
    "meme-cat",
    "meme-spoungebob",
    "meme-spiderman",
    "meme-no-ricky",
    "meme-penguin",
    "confused-face-meme",
    "happy-face-meme",
    "angry-face-meme",
    "crying-face-meme",
    "crazy-face-meme",
    "screaming-face-meme",
    "bart-simpson-simpsons-tv-cartoon-show",
    "homer-simpson-simpsons-tv-cartoon-show",
    "marge-simpson-simpsons-tv-cartoon-show",
    "lisa-simpson-simpsons-tv-cartoon-show",
    "maggie-simpson-simpsons-tv-cartoon-show",
    "bender-futurama-tv-cartoon-show",
    "fry-futurama-tv-cartoon-show",
    "leela-futurama-tv-cartoon-show",
    "professor-futurama-tv-cartoon-show",
    "pikachu-pokemon",
    "nathan-explosion-dethklok-metalocalypse",
    "Skwisgaar-Skwigelf-dethklok-metalocalypse",
    "toki-wartooth-dethklok-metalocalypse",
    "family-guy-stewie-griffin",
    "family-guy-peter-griffin",
    "family-guy-brian",
    "family-guy-lois-griffin",
    "family-guy-mag-griffin",
    "couple-in-the-beb-meme",
    "ronald-mcdonald-clown",
    "ned-flanders-simpsons",

    "soyjak",
    "guy-explaining-to-girl",
    "taylor-swift",
    "jesus-and-guy",
    "ozzy",
    "varg",
]

# todo: assess concept quality by average deviation from the centroid

async def main():
    model, preprocess, _ = open_clip.create_model_and_transforms(
        "ViT-B-32",
        pretrained="openai"
    )

    tokenizer = open_clip.get_tokenizer("ViT-B-32")

    device = "cuda" if torch.cuda.is_available() else "cpu"

    model = model.to(device)
    model.eval()

    def embed_text(text: str):
        tokens = tokenizer([text]).to(device)

        with torch.no_grad():
            features = model.encode_text(tokens)

        features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy()[0]

    async with AsyncSessionLocal() as session:
        print("Deleting all concept embeddings...")
        await session.execute(
            delete(Concept)
        )
        await session.commit()
        print("Done")

        print("Processing text concepts")
        for concept_name, concept_texts in concepts.items():

            concept_texts = [template.format(text) for text, template in product(concept_texts, templates)]

            vectors = [embed_text(t) for t in concept_texts]
            concept_embedding = np.mean(vectors, axis=0)
            concept_embedding /= np.linalg.norm(concept_embedding)

            # Medoid?
            similarities = [np.dot(vector, concept_embedding) for vector in vectors]
            average_similarity = np.mean(similarities)
            std_similarity = np.std(similarities)
            print(f"Concept: {concept_name}")
            print(f"Similarity: {average_similarity}")
            print(f"STD Similarity: {std_similarity}")

            session.add(Concept(name=concept_name, embedding=concept_embedding))

        print("Processing image concepts")
        for concept in image_concepts:
            vectors = []
            dir_path = os.path.join(pathlib.Path().resolve(), "images", concept)
            for image_file in os.listdir(dir_path):
                file_path = os.path.join(dir_path, image_file)
                try:
                    image = load_image(file_path)
                    vector = embed_image(image, device, model, preprocess)
                    vectors.append(vector)
                except Exception as e:
                    print(f"Can't load {file_path}: {e}")

            concept_embedding = np.mean(vectors, axis=0)
            concept_embedding /= np.linalg.norm(concept_embedding)

            # medoid?
            similarities = [np.dot(vector, concept_embedding) for vector in vectors]
            average_similarity = np.mean(similarities)
            std_similarity = np.std(similarities)
            print(f"Concept: {concept}")
            print(f"Similarity: {average_similarity}")
            print(f"STD Similarity: {std_similarity}")

            session.add(Concept(name=concept, embedding=concept_embedding))

        await session.commit()
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())