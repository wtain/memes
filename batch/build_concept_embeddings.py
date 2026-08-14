import argparse
import asyncio
import csv
import json
import os
import pathlib
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import Protocol

import numpy as np

from ai.clip import ClipModel
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings, load_env
from embeddingutils.image import load_image
from Storage.db import AsyncSessionLocal
from Storage.models import Concept, ConceptImageSet, ConceptImage
from repository.concepts import ConceptsRepository


class Embedder(Protocol):
    def embed_text(self, text: str) -> np.ndarray: ...
    def embed_image(self, image) -> np.ndarray: ...


@dataclass
class ConceptStats:
    name: str
    mean_similarity: float
    std_similarity: float


@dataclass
class EmbeddingResult:
    embedding: np.ndarray
    vectors: list[np.ndarray] = field(default_factory=list)


def build_centroid(vectors: list[np.ndarray]) -> np.ndarray:
    centroid = np.mean(vectors, axis=0)
    centroid /= np.linalg.norm(centroid)
    return centroid


def compute_stats(name: str, embedding: np.ndarray, vectors: list[np.ndarray]) -> ConceptStats:
    similarities = [np.dot(v, embedding) for v in vectors]
    return ConceptStats(
        name=name,
        mean_similarity=float(np.mean(similarities)),
        std_similarity=float(np.std(similarities)),
    )


class ConceptProcessor(ABC):
    def __init__(self, embedder: Embedder):
        self.embedder = embedder

    @abstractmethod
    def process(self) -> list[ConceptStats]:
        pass


class TextConceptProcessor(ConceptProcessor):
    def __init__(
        self,
        embedder: Embedder,
        concepts: dict[str, list[str]],
        templates: list[str],
        concepts_repo: ConceptsRepository,
    ):
        super().__init__(embedder)
        self.concepts = concepts
        self.templates = templates
        self.concepts_repo = concepts_repo

    def process(self) -> list[ConceptStats]:
        print("Processing text concepts")
        stats = []
        for name, texts in self.concepts.items():
            expanded = [tmpl.format(t) for t, tmpl in product(texts, self.templates)]
            vectors = [self.embedder.embed_text(t) for t in expanded]
            embedding = build_centroid(vectors)
            stats.append(compute_stats(name, embedding, vectors))
            self.concepts_repo.add(name, embedding)
        return stats


class ImageConceptProcessor(ConceptProcessor):
    def __init__(self, embedder: Embedder, images_path: str, session):
        super().__init__(embedder)
        self.images_path = images_path
        self.session = session

    def process(self) -> list[ConceptStats]:
        print("Processing image concepts")
        stats = []
        for concept_name in os.listdir(self.images_path):
            concept_stats = self._process_concept(concept_name)
            stats.extend(concept_stats)
        return stats

    def _process_concept(self, concept_name: str) -> list[ConceptStats]:
        print(f"Processing image concept {concept_name}")
        stats = []
        dir_path = os.path.join(self.images_path, concept_name)

        concept_entity = Concept(name=concept_name)
        main_image_set = ConceptImageSet(name="main", concept=concept_entity, directory=dir_path)

        all_vectors = []

        if os.path.isdir(dir_path):
            for entry in os.listdir(dir_path):
                entry_path = os.path.join(dir_path, entry)
                if os.path.isdir(entry_path):
                    subset_stats = self._process_image_set(
                        concept_name, entry, entry_path, concept_entity
                    )
                    stats.append(subset_stats.stats)
                    all_vectors.extend(subset_stats.vectors)
                else:
                    result = self._process_single_image(entry_path, entry, main_image_set)
                    if result:
                        all_vectors.append(result)
        else:
            result = self._process_single_image(dir_path, concept_name, main_image_set)
            if result is not None:
                all_vectors.append(result)

        concept_embedding = build_centroid(all_vectors)
        concept_entity.embedding = concept_embedding
        main_image_set.embedding = concept_embedding

        stats.append(compute_stats(concept_name, concept_embedding, all_vectors))

        self.session.add(concept_entity)
        self.session.add(main_image_set)

        return stats

    def _process_image_set(
        self, concept_name: str, set_name: str, dir_path: str, concept_entity: Concept
    ) -> 'ImageSetResult':
        image_set = ConceptImageSet(name=set_name, concept=concept_entity, directory=set_name)
        vectors = []

        for filename in os.listdir(dir_path):
            file_path = os.path.join(dir_path, filename)
            result = self._process_single_image(file_path, filename, image_set)
            if result is not None:
                vectors.append(result)

        embedding = build_centroid(vectors)
        image_set.embedding = embedding
        self.session.add(image_set)

        full_name = f"{concept_name}:{set_name}"
        return ImageSetResult(
            stats=compute_stats(full_name, embedding, vectors),
            vectors=vectors,
        )

    def _process_single_image(
        self, file_path: str, filename: str, image_set: ConceptImageSet
    ) -> np.ndarray | None:
        print(f"Processing file {filename}")
        try:
            image = load_image(file_path)
            vector = self.embedder.embed_image(image)
            image_entity = ConceptImage(image_set=image_set, embedding=vector, filename=filename)
            self.session.add(image_entity)
            return vector
        except Exception as e:
            print(f"Can't load {file_path}: {e}")
            return None


@dataclass
class ImageSetResult:
    stats: ConceptStats
    vectors: list[np.ndarray]


class StatsReporter:
    def __init__(self, output_dir: str = "./reports"):
        self.output_dir = output_dir

    def print_stats(self, stats: list[ConceptStats]) -> None:
        for s in stats:
            print(f"Concept: {s.name}")
            print(f"Similarity: {s.mean_similarity}")
            print(f"STD Similarity: {s.std_similarity}")

    def save_to_csv(self, stats: list[ConceptStats]) -> str:
        os.makedirs(self.output_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = os.path.join(self.output_dir, f"image_concept_stats_{timestamp}.csv")

        with open(file_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["concept", "mean_similarity", "std_similarity"])
            for s in sorted(stats, key=lambda x: x.mean_similarity, reverse=True):
                writer.writerow([s.name, s.mean_similarity, s.std_similarity])

        return file_path


class ConceptEmbeddingBuilder:
    def __init__(
        self,
        embedder: Embedder,
        text_concepts: dict[str, list[str]],
        templates: list[str],
        images_path: str,
        reporter: StatsReporter | None = None,
    ):
        self.embedder = embedder
        self.text_concepts = text_concepts
        self.templates = templates
        self.images_path = images_path
        self.reporter = reporter or StatsReporter()

    async def build(self) -> None:
        async with AsyncSessionLocal() as session:
            concepts_repo = ConceptsRepository(session)
            await concepts_repo.delete_all()

            text_processor = TextConceptProcessor(
                self.embedder, self.text_concepts, self.templates, concepts_repo
            )
            text_stats = text_processor.process()
            self.reporter.print_stats(text_stats)

            image_processor = ImageConceptProcessor(
                self.embedder, self.images_path, session
            )
            image_stats = image_processor.process()

            await session.commit()
            print("Done")

            self.reporter.print_stats(image_stats)
            csv_path = self.reporter.save_to_csv(image_stats)
            print(f"Report saved to: {csv_path}")


def load_json(path: str) -> dict | list:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def _process() -> None:
    text_concepts_file = settings.get("CONCEPTS.TEXT_CONCEPTS_FILE")
    templates_file = settings.get("CONCEPTS.TEXT_CONCEPTS_TEMPLATES_FILE")
    images_dir = settings.get("CONCEPTS.IMAGES_DIR")

    text_concepts = load_json(text_concepts_file)
    templates = load_json(templates_file)

    working_directory = pathlib.Path().resolve()
    images_path = os.path.join(working_directory, images_dir)

    builder = ConceptEmbeddingBuilder(
        embedder=ClipModel(),
        text_concepts=text_concepts,
        templates=templates,
        images_path=images_path,
    )
    await builder.build()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_concept_embeddings", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior