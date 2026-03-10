import asyncio

import numpy as np
import open_clip
import torch
from sqlalchemy import delete

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
        "people drawing scary faces with a black paint"
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
    ]
}

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

        print("Processing concepts")
        for concept_name, concept_texts in concepts.items():

            vectors = [embed_text(t) for t in concept_texts]
            concept_embedding = np.mean(vectors, axis=0)

            concept_embedding /= np.linalg.norm(concept_embedding)
            session.add(Concept(name=concept_name, embedding=concept_embedding))

        await session.commit()
        print("Done")


if __name__ == "__main__":
    asyncio.run(main())