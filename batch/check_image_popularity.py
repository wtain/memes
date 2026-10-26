"""
Checks images.popularity against the value derived from the open popularity_absorptions edges.
Without --fix only reports drift; with --fix rewrites the cached value to the expected one.
Not run-tracked. See docs/superpowers/specs/2026-10-26-meme-popularity/.
"""
import argparse
import asyncio

from sqlalchemy import update

from config.settings import load_env
from repository.popularity import Drift, PopularityRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


async def run(session, fix: bool) -> list[Drift]:
    """Return the drift found; when fix, rewrite images.popularity to expected. Never commits."""
    drifts = await PopularityRepository(session).verify()
    if fix:
        for drift in drifts:
            await session.execute(
                update(Image).where(Image.id == drift.image_id).values(popularity=drift.expected)
            )
    return drifts


async def main(fix: bool) -> None:
    async with AsyncSessionLocal() as session:
        drifts = await run(session, fix)
        if fix and drifts:
            await session.commit()
    for d in drifts:
        print(f"{d.image_id}: cached={d.cached} expected={d.expected}")
    print(f"{len(drifts)} drifted image(s)" + (" fixed" if fix else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(args.fix))
