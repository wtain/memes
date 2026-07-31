import argparse
import asyncio
import uuid
from collections import Counter

import pymorphy3

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from batch.trends.connectors.registry import get_connector
from batch.trends.processing import Processor
from batch.trends.resolution import resolve_labels, resolve_language, resolve_model
from repository.trends import TrendSourceRepository, TrendsRunResultRepository
from rules.normalize import LEMMATIZABLE_LANGUAGES, lemmatize_phrase, make_morph


def process_source(source, connector, processor: Processor, labels: list[str], model_name: str,
                    language: str | None = None, morph: pymorphy3.MorphAnalyzer | None = None) -> Counter:
    trends = Counter()
    data = connector.fetch()
    print(f"Scraping {source.name}")
    for item in data:
        print("\n---")
        title = item["title"]
        print(title)
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            if language in LEMMATIZABLE_LANGUAGES:
                entity_text = lemmatize_phrase(entity_text, morph)
            trends[f"{label}:{entity_text}"] += 1
    return trends


async def run(session, run_id: uuid.UUID) -> None:
    processor = Processor()
    morph = make_morph()

    sources_repo = TrendSourceRepository(session)
    sources = await sources_repo.get_all()

    results_repo = TrendsRunResultRepository(session, run_id)

    for source in sources:
        connector = get_connector(source.name, source.connector_type, source.config)
        labels = resolve_labels(source, settings)
        model_name = resolve_model(source, settings)
        language = resolve_language(source, settings)

        trends = process_source(source, connector, processor, labels, model_name, language, morph)

        for topic, value in trends.items():
            label, name = topic.split(":", 1)
            await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    # TrendsRunResultRepository.add_result() only add()s + flush()es -- per this repo's
    # convention, repositories never commit(), callers do. The old inlined main() covered
    # this with a `finally: await session.commit()` around its whole try/except, so results
    # written before a mid-run failure were still persisted. Match that here: commit in a
    # finally so partial results survive a `run()` exception too, not just the success path.
    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                try:
                    await run(session, run_id)
                finally:
                    await session.commit()
    else:
        async with tracked_run(kind="trends", trigger=trigger) as new_run_id:
            async with AsyncSessionLocal() as session:
                try:
                    await run(session, new_run_id)
                finally:
                    await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
