import argparse
import asyncio
import json

from config.settings import settings, load_env
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from repository.concepts import ConceptsRepository
from repository.tags import TagsRepository, TagsSaver


async def main():
    TAG_KIND = "CONCEPT"

    mapping_file = settings.get("CONCEPTS.MAPPING_FILE")
    default_threshold = settings.CONCEPTS.THRESHOLD
    default_limit = settings.CONCEPTS.LIMIT

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
