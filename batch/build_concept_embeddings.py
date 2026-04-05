import asyncio
import csv
import os
import pathlib
from datetime import datetime
from itertools import product

import numpy as np
import open_clip
import torch
from sqlalchemy import delete

from batch.embeddingutils.image import load_image, embed_image
from batch.models.external import AsyncSessionLocal
from batch.models.external import ImageTag, OCRText, Embedding, Concept, ConceptImageSet, ConceptImage


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
            concept_embedding = build_centroid(vectors)

            # Medoid?
            report_statistics(concept_embedding, concept_name, vectors)

            session.add(Concept(name=concept_name, embedding=concept_embedding))

        working_directory = pathlib.Path().resolve()
        images_path = os.path.join(working_directory, "images")

        image_concept_stats = {}

        print("Processing image concepts")
        for concept_name in os.listdir(images_path):
        # if True:
        #     if concept_name == "_bad":
        #         continue
        #     if concept_name == "mikael-akerfeldt" or concept_name == "chad-kroeger":
        #         continue
            print(f"Processing image concept {concept_name}")
            vectors = []
            dir_path = os.path.join(images_path, concept_name)

            concept_entity = Concept(name=concept_name)
            image_set_entity_main = ConceptImageSet(name="main", concept=concept_entity, directory=dir_path)

            for image_file_main in os.listdir(dir_path):
                file_path = os.path.join(dir_path, image_file_main)
                if os.path.isdir(file_path):
                    image_set_entity = ConceptImageSet(name=image_file_main, concept=concept_entity, directory=image_file_main)
                    image_set_vectors = []
                    for image_file in os.listdir(file_path):
                        image_file_path = os.path.join(file_path, image_file)
                        process_image_file(device, image_file_path, image_file, image_set_entity, model, preprocess,
                                           session, image_set_vectors)

                    image_set_embedding = build_centroid(image_set_vectors)
                    image_set_entity.embedding = image_set_embedding
                    session.add(image_set_entity)

                    sub_concept_name = f"{concept_name}:{image_file_main}"
                    average_similarity, std_similarity = report_statistics(image_set_embedding, sub_concept_name, image_set_vectors)
                    image_concept_stats[sub_concept_name] = (average_similarity, std_similarity)
                    vectors += image_set_vectors
                else:
                    process_image_file(device, file_path, image_file_main, image_set_entity_main, model, preprocess,
                                             session, vectors)

            concept_embedding = build_centroid(vectors)
            concept_entity.embedding = concept_embedding
            image_set_entity_main.embedding = concept_embedding

            # medoid?
            average_similarity, std_similarity = report_statistics(concept_embedding, concept_name, vectors)
            image_concept_stats[concept_name] = (average_similarity, std_similarity)

            session.add(concept_entity)
            session.add(image_set_entity_main)

        await session.commit()
        print("Done")

        for concept_name in image_concept_stats.keys():
            mean_sim, std_sim = image_concept_stats[concept_name]
            print(f"{concept_name}: {mean_sim} {std_sim}")
        csv_path = save_image_concept_stats_to_csv(image_concept_stats, output_dir="./reports")
        print(f"Report saved to: {csv_path}")


def save_image_concept_stats_to_csv(stats: dict, output_dir: str = ".") -> str:
    """
    Save image_concept_stats to a CSV file.

    :param stats: dict like {concept_name: (mean_similarity, std_similarity)}
    :param output_dir: directory where CSV will be saved
    :return: full path to the created file
    """

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Timestamp with seconds
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = f"image_concept_stats_{timestamp}.csv"
    file_path = os.path.join(output_dir, filename)

    # Write CSV
    with open(file_path, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)

        # Header
        writer.writerow(["concept", "mean_similarity", "std_similarity"])

        # Rows
        for concept, (mean_sim, std_sim) in sorted(stats.items(), key=lambda x: x[1][0], reverse=True):
            writer.writerow([concept, mean_sim, std_sim])

    return file_path


def process_image_file(device, file_path, image_file, image_set_entity, model, preprocess, session, vectors):
    print(f"Processing file {image_file}")
    try:
        image = load_image(file_path)
        vector = embed_image(image, device, model, preprocess)
        vectors.append(vector)
        image_entity = ConceptImage(image_set=image_set_entity, embedding=vector, filename=image_file)
        session.add(image_entity)
    except Exception as e:
        print(f"Can't load {file_path}: {e}")


def report_statistics(concept_embedding, concept_name, vectors):
    similarities = [np.dot(vector, concept_embedding) for vector in vectors]
    average_similarity = np.mean(similarities)
    std_similarity = np.std(similarities)
    print(f"Concept: {concept_name}")
    print(f"Similarity: {average_similarity}")
    print(f"STD Similarity: {std_similarity}")
    return average_similarity, std_similarity


def build_centroid(vectors):
    concept_embedding = np.mean(vectors, axis=0)
    concept_embedding /= np.linalg.norm(concept_embedding)
    return concept_embedding


if __name__ == "__main__":
    asyncio.run(main())