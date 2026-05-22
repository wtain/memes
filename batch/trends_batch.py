import asyncio
from collections import Counter

from Storage.db import AsyncSessionLocal
from batch.trends.processing import Processor
from batch.trends.scraping import RSSScraper
from repository.trends import FeedSourceRepository, TrendsRunRepository, TrendsRunResultRepository


async def main():

    async with AsyncSessionLocal() as session:

        sources_repo = FeedSourceRepository(session)
        sources = await sources_repo.get_all()

        runs_repo = TrendsRunRepository(session)

        run_id = await runs_repo.create_run()

        results_repo = TrendsRunResultRepository(session, run_id)

        try:
            processor = Processor()

            for source in sources:
                trends = Counter()
                scraper = RSSScraper(source.name, source.url, source.selector)
                data = scraper.parse()
                print(f"Scraping {source.name}")
                for item in data:
                    print("\n---")
                    title = item["title"]
                    print(title)
                    text = item["text"]
                    for text, label in processor.process(text):
                        trends[f"{label}:{text}"] += 1

                for topic in trends:
                    label, name = topic.split(":")
                    await results_repo.add_result(source_id=source.id, label=label, name=name, value=trends[topic])

            await runs_repo.commit(run_id)
        except Exception:
            await runs_repo.fail(run_id)
            raise
        finally:
            await session.commit()


if __name__ == "__main__":
    asyncio.run(main())