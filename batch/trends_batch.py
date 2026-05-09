import asyncio
import uuid
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import RunStatus
from batch.models.external import AsyncSessionLocal, FeedSource, TrendsRun, TrendsRunResult
from batch.trends.processing import Processor
from batch.trends.scraping import RSSScraper


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


class FeedSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> list[FeedSource]:
        result = await self._session.execute(select(FeedSource))
        return list(result.scalars().all())


class TrendsRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(self) -> uuid.UUID:
        run = TrendsRun(status=str(RunStatus.started))
        self._session.add(run)
        await self._session.flush()  # populates run_id without closing the transaction
        return run.run_id

    async def commit(self, run_id: uuid.UUID) -> None:
        await self._set_status(run_id, RunStatus.completed)

    async def fail(self, run_id: uuid.UUID) -> None:
        await self._set_status(run_id, RunStatus.failed)

    async def _set_status(self, run_id: uuid.UUID, status: RunStatus) -> None:
        result = await self._session.execute(
            select(TrendsRun).where(TrendsRun.run_id == run_id)
        )
        run = result.scalar_one()
        run.status = str(status)
        await self._session.flush()


class TrendsRunResultRepository:
    def __init__(self, session: AsyncSession, run_id: uuid.UUID) -> None:
        self._session = session
        self._run_id = run_id

    async def add_result(self, source_id: int, label: str, name: str, value: int) -> TrendsRunResult:
        result = TrendsRunResult(
            run_id=self._run_id,
            source_id=source_id,
            label=label,
            name=name,
            value=value,
        )
        self._session.add(result)
        await self._session.flush()
        return result


if __name__ == "__main__":
    asyncio.run(main())