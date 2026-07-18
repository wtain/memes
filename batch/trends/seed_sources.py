import argparse
import asyncio

from config.settings import load_env
from Storage.db import AsyncSessionLocal
from Storage.models import TrendSource
from repository.trends import TrendSourceRepository

MEDUZA_SOURCE = {
    "name": "Meduza",
    "connector_type": "api",
    "extraction": {"language": "ru"},
    "config": {
        "base_url": "https://meduza.io/api/w5/new_search",
        "locale": "ru",
        "per_page": 100,
        "num_pages": 100,
        "sleep_every_pages": 10,
        "sleep_seconds": 10,
    },
}


async def seed_meduza_source(session) -> bool:
    repo = TrendSourceRepository(session)
    existing = await repo.get_all()
    existing_source = next((s for s in existing if s.name == MEDUZA_SOURCE["name"]), None)

    if existing_source is not None:
        # Sync extraction (e.g. language) on rows seeded before this field existed —
        # config/connector_type are left alone since they may hold production-tuned
        # values (rate-limiting knobs) that shouldn't be silently overwritten.
        if existing_source.extraction != MEDUZA_SOURCE["extraction"]:
            existing_source.extraction = MEDUZA_SOURCE["extraction"]
            await session.flush()
            print(f"{MEDUZA_SOURCE['name']} source already exists, updated extraction")
        else:
            print(f"{MEDUZA_SOURCE['name']} source already exists, skipping")
        return False

    session.add(TrendSource(**MEDUZA_SOURCE))
    await session.flush()
    print(f"Added {MEDUZA_SOURCE['name']} source")
    return True


async def main() -> None:
    async with AsyncSessionLocal() as session:
        await seed_meduza_source(session)
        await session.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
