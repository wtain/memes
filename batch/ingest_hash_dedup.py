"""
Ingestion stage 1: hash-based dedup of a new image batch, before any embeddings exist.

See docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md and
docs/superpowers/specs/2026-08-08-ingestion-hash-dedup-incremental-design.md. This is
Stage 0 (intake) + Stage 1 (hash dedup) only -- Tier A/B near-duplicate review
(embeddings, OCR, human review) are later phases, not implemented here.

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

Safe to re-run at any point while an ingestion run is active -- rather than refusing, this
script joins the active run (reusing its batch_id, accumulating stats across invocations)
so newly-dropped files can be added to an in-progress batch. Newly-added pending images
need the rest of the pipeline re-run to get review coverage: extract_text_from_memes.py
--status pending, then ingest_find_duplicates.py for whichever tier(s) are relevant --
both are already safe to re-run against the same batch (see CLAUDE.md's ingestion pipeline
section). A Postgres advisory lock (acquire_run_lock) serializes concurrent invocations of
this script against each other, so two operators re-joining the same run at once don't race
on PATH_INGESTION_SOURCE's filesystem state.

Known limitation: matches trends_batch.py's crash-safety posture, not a stricter one --
the batch_runs row and whatever registrations/moves happened before a failure are
committed regardless (via a `finally: await session.commit()`). A failure while creating a
brand-new run marks it `failed`; a failure while joining an already-active run leaves it
`started` (not `failed`) so a possibly-partially-reviewed-or-promoted batch isn't destroyed
by a Stage-1 re-run error -- see resolve_batch's `is_new_run` return value. Either way,
already-registered pending images survive as-is rather than being rolled back. Not
addressed further here -- not worth over-engineering before this pipeline has run against
real data at volume.
"""
import argparse
import asyncio
import os
import shutil

from sqlalchemy import select, text

from batch.utils.file_hash import sha256_file
from batch.utils.safe_move import move_without_overwrite
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
    """Move each survivor's file into base_path (renaming on a filename collision --
    the ingestion inbox and the active library can share a filename despite having
    different content, since identical-content files were already caught by hash-based
    dedup above) and register it as a pending Image row using whichever filename it
    actually ended up with there."""
    images_repo = ImagesRepository(session)
    registered_ids = []
    os.makedirs(base_path, exist_ok=True)
    for filename, content_hash in survivors.items():
        final_filename = move_without_overwrite(os.path.join(source_path, filename), base_path)
        if final_filename != filename:
            print(f"  renamed to avoid overwrite: {filename} -> {final_filename}")
        image = await images_repo.register_image(
            final_filename, status="pending", content_hash=content_hash, ingestion_batch_id=batch_id,
        )
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


async def acquire_run_lock(session) -> bool:
    """Postgres advisory lock scoped to the current transaction, released automatically at
    commit/rollback. Serializes concurrent ingest_hash_dedup.py invocations against each
    other for this environment (each environment is a separate Postgres instance, so one
    fixed key needs no environment-scoping) -- without it, two operators re-joining the
    same active run at once could race on PATH_INGESTION_SOURCE's filesystem state."""
    return (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(hashtext('ingest_hash_dedup')::bigint)")
    )).scalar_one()


async def resolve_batch(runs_repo: BatchRunRepository) -> tuple:
    """Reuse the currently active ingestion run if one exists (letting newly-dropped files
    join the same batch instead of being blocked), or start a new one. Returns
    (batch_id, existing_stats, is_new_run)."""
    active_run = await runs_repo.get_active_run(kind="ingestion")
    if active_run is not None:
        print(f"Joining active ingestion run {active_run.run_id} (stage={active_run.stage})")
        return active_run.run_id, (active_run.stats or {}), False
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    return batch_id, {}, True


def accumulate_stats(existing: dict, new: dict) -> dict:
    """Add this invocation's counts on top of whatever the batch has accumulated so far, so
    re-running Stage 1 against an already-active run reports running totals instead of
    silently overwriting earlier numbers -- BatchRunRepository.update_stats() itself merges
    by overwrite, not by sum, so this has to happen before calling it."""
    return {key: existing.get(key, 0) + value for key, value in new.items()}


async def main(env: str | None) -> None:
    load_env(env)
    source_path = settings.get("PATH_INGESTION_SOURCE")
    if not source_path:
        raise RuntimeError("PATH_INGESTION_SOURCE is required but not set")
    base_path = settings.BASE_PATH

    async with AsyncSessionLocal() as session:
        if not await acquire_run_lock(session):
            raise RuntimeError(
                "Another ingest_hash_dedup.py run is already in progress for this "
                "environment -- try again shortly."
            )

        runs_repo = BatchRunRepository(session)
        batch_id, existing_stats, is_new_run = await resolve_batch(runs_repo)

        stats = None
        try:
            stats = await run(session, source_path, base_path, batch_id)
            # Not runs_repo.commit() -- that would mark the *whole* ingestion run
            # completed, but Stage 1 is only the first of several stages spanning
            # multiple later script invocations and human review. The run stays
            # `started` (and so still blocks a second concurrent run, correctly) until
            # promotion -- not yet implemented -- finishes it.
            await runs_repo.update_stats(batch_id, **accumulate_stats(existing_stats, stats))
        except Exception as e:
            if is_new_run:
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
