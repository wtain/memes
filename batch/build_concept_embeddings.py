import asyncio
import csv
import json
import os
import pathlib
from datetime import datetime
from itertools import product

import numpy as np

from ai.clip import ClipModel
from embeddingutils.image import load_image
from Storage.db import AsyncSessionLocal
from Storage.models import Concept, ConceptImageSet, ConceptImage
from repository.concepts import ConceptsRepository



# concept name -> list of texts
# later we add images

TEXT_CONCEPTS_FILE = os.getenv('TEXT_CONCEPTS_FILE')
TEXT_CONCEPTS_TEMPLATES_FILE = os.getenv('TEXT_CONCEPTS_TEMPLATES_FILE')
CONCEPT_IMAGES_DIR = os.getenv('CONCEPT_IMAGES_DIR')

with open(TEXT_CONCEPTS_FILE, "r", encoding='utf-8') as jsonfile:
    concepts = json.load(jsonfile)

with open(TEXT_CONCEPTS_TEMPLATES_FILE, "r", encoding='utf-8') as jsonfile:
    templates = json.load(jsonfile)


# todo: assess concept quality by average deviation from the centroid

async def main():

    clip_model = ClipModel()

    async with AsyncSessionLocal() as session:

        concepts_repo = ConceptsRepository(session)

        await concepts_repo.delete_all()

        process_text_concepts(clip_model, concepts_repo)

        working_directory = pathlib.Path().resolve()
        images_path = os.path.join(working_directory, CONCEPT_IMAGES_DIR)

        image_concept_stats = {}

        print("Processing image concepts")
        for concept_name in os.listdir(images_path):
            print(f"Processing image concept {concept_name}")
            vectors = []
            dir_path = os.path.join(images_path, concept_name)

            concept_entity = Concept(name=concept_name)
            image_set_entity_main = ConceptImageSet(name="main", concept=concept_entity, directory=dir_path)

            if os.path.isdir(dir_path):
                for image_file_main in os.listdir(dir_path):
                    file_path = os.path.join(dir_path, image_file_main)
                    if os.path.isdir(file_path):
                        image_set_entity = ConceptImageSet(name=image_file_main, concept=concept_entity, directory=image_file_main)
                        image_set_vectors = []
                        for image_file in os.listdir(file_path):
                            image_file_path = os.path.join(file_path, image_file)
                            process_image_file(clip_model, image_file_path, image_file, image_set_entity,
                                               session, image_set_vectors)

                        image_set_embedding = build_centroid(image_set_vectors)
                        image_set_entity.embedding = image_set_embedding
                        session.add(image_set_entity)

                        sub_concept_name = f"{concept_name}:{image_file_main}"
                        average_similarity, std_similarity = report_statistics(image_set_embedding, sub_concept_name, image_set_vectors)
                        image_concept_stats[sub_concept_name] = (average_similarity, std_similarity)
                        vectors += image_set_vectors
                    else:
                        process_image_file(clip_model, file_path, image_file_main, image_set_entity_main, session, vectors)
            else:
                process_image_file(clip_model, dir_path, concept_name, image_set_entity_main, session, vectors)

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


def process_text_concepts(clip_model, concepts_repo):
    print("Processing text concepts")
    for concept_name, concept_texts in concepts.items():
        concept_texts = [template.format(text) for text, template in product(concept_texts, templates)]

        vectors = [clip_model.embed_text(t) for t in concept_texts]
        concept_embedding = build_centroid(vectors)

        # Medoid?
        report_statistics(concept_embedding, concept_name, vectors)

        concepts_repo.add(concept_name, concept_embedding)


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


def process_image_file(clip_model, file_path, image_file, image_set_entity, session, vectors):
    print(f"Processing file {image_file}")
    try:
        image = load_image(file_path)
        vector = clip_model.embed_image(image)
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