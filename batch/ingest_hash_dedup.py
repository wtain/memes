"""
Ingestion stage 1: hash-based dedup of a new image batch, before any embeddings exist.

See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md. This is Stage 0
(intake) + Stage 1 (hash dedup) only -- Tier A/B near-duplicate review (embeddings, OCR,
human review) are later phases, not implemented here.

Flow, for every regular file directly in PATH_INGESTION_SOURCE:
  1. In-batch: files with identical content hashes are deduped, keeping one (lexicographic
     order -- they're byte-identical, so it doesn't matter which); the rest move to
     PATH_INGESTION_SOURCE/duplicates/.
  2. Cross-corpus: survivors whose hash matches an existing *active* image's content_hash
     also move to duplicates/ -- same tier as in-batch matches, both are exact-hash
     decisions, just compared against a different set.
  3. Remaining survivors are registered as `pending` Image rows (content_hash stored at
     registration time, so this check never needs to re-hash the existing corpus on a
     future run) and their files move into BASE_PATH, same filename, ready for Tier A.

Known limitation: matches trends_batch.py's crash-safety posture, not a stricter one --
the batch_runs row and whatever registrations/moves happened before a failure are
committed regardless (via a `finally: await session.commit()`), so the run is marked
`failed` but any already-registered pending images survive as-is rather than being rolled
back. Not addressed further here -- not worth over-engineering before this pipeline has
run against real data.
"""
import argparse
import asyncio
import os
import shutil

from sqlalchemy import select

from batch.utils.file_hash import sha256_file
from config.settings import load_env, settings
from repository.batch_runs import BatchRunRepository
from repository.images import ImagesRepository
from Storage.db import AsyncSessionLocal
from Storage.models import Image


def hash_incoming_files(source_path: str) -> dict[str, list[str]]:
    """Hash every regular file directly in source_path (non-recursive). Returns
    content_hash -> [filenames]."""
    hash_to_files: dict[str, list[str]] = {}
    for filename in os.listdir(source_path):
        full_path = os.path.join(source_path, filename)
        if os.path.isdir(full_path):
            continue
        content_hash = sha256_file(full_path)
        hash_to_files.setdefault(content_hash, []).append(filename)
    return hash_to_files


def dedupe_in_batch(source_path: str, hash_to_files: dict[str, list[str]], duplicates_dir: str) -> dict[str, str]:
    """Keep one file per hash (lexicographically first filename), move the rest to
    duplicates_dir. Returns filename -> content_hash for survivors."""
    survivors: dict[str, str] = {}
    for content_hash, filenames in hash_to_files.items():
        filenames_sorted = sorted(filenames)
        keeper = filenames_sorted[0]
        survivors[keeper] = content_hash
        for dup in filenames_sorted[1:]:
            os.makedirs(duplicates_dir, exist_ok=True)
            print(f"  in-batch duplicate: {dup} (same content as {keeper})")
            shutil.move(os.path.join(source_path, dup), os.path.join(duplicates_dir, dup))
    return survivors


async def dedupe_cross_corpus(
    session, source_path: str, survivors: dict[str, str], duplicates_dir: str
) -> dict[str, str]:
    """Drop any survivor whose hash already matches an active image in the corpus, moving
    it to duplicates_dir. Returns filename -> content_hash for the remainder."""
    if not survivors:
        return survivors

    result = await session.execute(
        select(Image.content_hash).where(
            Image.status == "active",
            Image.content_hash.in_(set(survivors.values())),
        )
    )
    existing_hashes = {row[0] for row in result.all()}
    if not existing_hashes:
        return survivors

    remaining: dict[str, str] = {}
    for filename, content_hash in survivors.items():
        if content_hash in existing_hashes:
            os.makedirs(duplicates_dir, exist_ok=True)
            print(f"  cross-corpus duplicate: {filename} already in the active library")
            shutil.move(os.path.join(source_path, filename), os.path.join(duplicates_dir, filename))
        else:
            remaining[filename] = content_hash
    return remaining


async def register_and_move_to_base_path(
    session, source_path: str, base_path: str, survivors: dict[str, str], batch_id
) -> list:
    """Register each survivor as a pending Image row and move its file into base_path
    (same filename -- extract_text_from_memes.py later depends on that as its lookup
    key). Returns the list of registered image ids."""
    images_repo = ImagesRepository(session)
    registered_ids = []
    os.makedirs(base_path, exist_ok=True)
    for filename, content_hash in survivors.items():
        image = await images_repo.register_image(
            filename, status="pending", content_hash=content_hash, ingestion_batch_id=batch_id,
        )
        shutil.move(os.path.join(source_path, filename), os.path.join(base_path, filename))
        registered_ids.append(image.id)
    return registered_ids


async def run(session, source_path: str, base_path: str, batch_id) -> dict:
    """Stage 1 end to end. Returns stats for the caller to record on the batch_runs row."""
    duplicates_dir = os.path.join(source_path, "duplicates")

    hash_to_files = hash_incoming_files(source_path)
    intake_count = sum(len(v) for v in hash_to_files.values())

    survivors = dedupe_in_batch(source_path, hash_to_files, duplicates_dir)
    hash_duplicates_in_batch = intake_count - len(survivors)

    survivors = await dedupe_cross_corpus(session, source_path, survivors, duplicates_dir)
    hash_duplicates_cross_corpus = intake_count - hash_duplicates_in_batch - len(survivors)

    registered_ids = await register_and_move_to_base_path(session, source_path, base_path, survivors, batch_id)

    return {
        "intake": intake_count,
        "hash_duplicates_in_batch": hash_duplicates_in_batch,
        "hash_duplicates_cross_corpus": hash_duplicates_cross_corpus,
        "registered": len(registered_ids),
    }


async def main(env: str | None) -> None:
    load_env(env)
    source_path = settings.get("PATH_INGESTION_SOURCE")
    if not source_path:
        raise RuntimeError("PATH_INGESTION_SOURCE is required but not set")
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        runs_repo = BatchRunRepository(session)

        active_run = await runs_repo.get_active_run(kind="ingestion")
        if active_run is not None:
            raise RuntimeError(
                f"An ingestion run is already in progress (run_id={active_run.run_id}, "
                f"stage={active_run.stage}) -- finish or abandon it before starting a new one."
            )

        batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")
        stats = None
        try:
            stats = await run(session, source_path, base_path, batch_id)
            await runs_repo.commit(batch_id, stats=stats)
        except Exception as e:
            await runs_repo.fail(batch_id, error=str(e))
            raise
        finally:
            await session.commit()

    print(f"Ingestion run {batch_id}: {stats}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    asyncio.run(main(args.env))
