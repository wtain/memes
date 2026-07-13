import argparse
import asyncio
from collections import Counter

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from batch.trends.connectors.registry import get_connector
from batch.trends.processing import Processor
from batch.trends.resolution import resolve_labels, resolve_model
from repository.trends import TrendSourceRepository, TrendsRunRepository, TrendsRunResultRepository


def process_source(source, connector, processor: Processor, labels: list[str], model_name: str) -> Counter:
    trends = Counter()
    data = connector.fetch()
    print(f"Scraping {source.name}")
    for item in data:
        print("\n---")
        title = item["title"]
        print(title)
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            trends[f"{label}:{entity_text}"] += 1
    return trends


async def main():
    processor = Processor()

    async with AsyncSessionLocal() as session:

        sources_repo = TrendSourceRepository(session)
        sources = await sources_repo.get_all()

        runs_repo = TrendsRunRepository(session)

        run_id = await runs_repo.create_run()

        results_repo = TrendsRunResultRepository(session, run_id)

        try:
            for source in sources:
                connector = get_connector(source.name, source.connector_type, source.config)
                labels = resolve_labels(source, settings)
                model_name = resolve_model(source, settings)

                trends = process_source(source, connector, processor, labels, model_name)

                for topic, value in trends.items():
                    label, name = topic.split(":", 1)
                    await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)

            await runs_repo.commit(run_id)
        except Exception:
            await runs_repo.fail(run_id)
            raise
        finally:
            await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
