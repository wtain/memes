import asyncio
import json
import os

from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.concepts import ConceptsRepository
from repository.tags import TagsRepository, TagsSaver

DEFAULT_THRESHOLD = 0.2
DEFAULT_LIMIT = 50


async def main():
    TAG_KIND = "CONCEPT"

    mapping_file = os.getenv("CONCEPT_MAPPING_FILE")
    default_threshold = float(os.getenv("CONCEPT_THRESHOLD", str(DEFAULT_THRESHOLD)))
    default_limit = int(os.getenv("CONCEPT_LIMIT", str(DEFAULT_LIMIT)))

    print(f"CONCEPT_MAPPING_FILE={mapping_file}")
    print(f"default threshold={default_threshold}, default limit={default_limit}")

    with open(mapping_file) as f:
        mapping = json.load(f)

    metrics = SimpleMetricsListener()

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        await tags_repo.delete_tags(TAG_KIND)

        concept_repo = ConceptsRepository(session)
        concepts = await concept_repo.get_all()

        async with TagsSaver(session) as tags_saver:
            for concept_id, concept_name in concepts:
                tag_def = mapping.get(concept_name)
                if tag_def is None:
                    print(f"No mapping for concept '{concept_name}', skipping")
                    metrics.increment("skipped.no_mapping")
                    continue

                tag_key = tag_def["key"]
                tag_value = tag_def["value"]
                threshold = tag_def.get("threshold", default_threshold)
                limit = tag_def.get("limit", default_limit)

                print(f"Processing concept '{concept_name}' -> {tag_key}:{tag_value} (threshold={threshold}, limit={limit})")

                images = await concept_repo.top_images_for_concept(
                    concept_id, threshold=threshold, limit=limit
                )

                for image_id, filename, match_count, avg_distance, score in images:
                    tags_saver.add_tag(image_id, tag_key, tag_value, TAG_KIND)
                    metrics.increment("tagged.images")

                metrics.increment("processed.concepts")

    metrics.print()


if __name__ == "__main__":
    asyncio.run(main())