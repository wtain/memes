import argparse
import asyncio

from sqlalchemy import delete, func, select

from config.settings import load_env
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from Storage.models import TmpImageClusters


async def run(session) -> SimpleMetricsListener:
    metrics = SimpleMetricsListener()

    singleton_cluster_ids = (
        select(TmpImageClusters.cluster_id)
        .group_by(TmpImageClusters.cluster_id)
        .having(func.count() == 1)
    )
    result = await session.execute(
        delete(TmpImageClusters).where(TmpImageClusters.cluster_id.in_(singleton_cluster_ids))
    )
    metrics.add("removed", result.rowcount)
    return metrics


async def main() -> None:
    async with AsyncSessionLocal() as session:
        metrics = await run(session)
        await session.commit()
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
