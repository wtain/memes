import argparse
import asyncio
import os
import shutil

from batch.utils.file_hash import files_are_identical, sha256_file
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from metrics.listener import SimpleMetricsListener
from repository.images import ImagesRepository


def _index_reference_dir(reference_dir: str) -> dict:
    """hash -> list of paths for every top-level file in reference_dir."""
    index: dict = {}
    for file in os.listdir(reference_dir):
        path = os.path.join(reference_dir, file)
        if os.path.isdir(path):
            continue
        index.setdefault(sha256_file(path), []).append(path)
    return index


async def run(session, base_path: str, reference_dir: str, dry_run: bool, metrics: SimpleMetricsListener):
    images_repo = ImagesRepository(session)

    print(f"Indexing reference_dir={reference_dir}")
    reference_index = _index_reference_dir(reference_dir)
    print(f"Reference files hashed: {sum(len(v) for v in reference_index.values())}")

    dest_dir = os.path.join(base_path, "possible_duplicates")
    if not dry_run:
        os.makedirs(dest_dir, exist_ok=True)

    for file in os.listdir(base_path):
        path = os.path.join(base_path, file)

        if os.path.isdir(path):
            metrics.increment("skipped.directory")
            continue
        if file.lower().endswith(".mp4"):
            metrics.increment("skipped.video")
            continue

        if await images_repo.find_image_by_filename(file):
            metrics.increment("skipped.registered")
            continue

        metrics.increment("candidates.unregistered")

        content_hash = sha256_file(path)
        reference_matches = reference_index.get(content_hash)
        if not reference_matches:
            continue

        if not files_are_identical([path, reference_matches[0]]):
            print(f"  WARNING: hash collision but content differs for {file} — leaving in place")
            metrics.increment("warning.hash_collision_mismatch")
            continue

        metrics.increment("duplicate.found")
        if dry_run:
            print(f"  [dry-run] would move {file} -> possible_duplicates/")
            metrics.increment("duplicate.would_move")
        else:
            print(f"  moving {file} -> possible_duplicates/")
            shutil.move(path, os.path.join(dest_dir, file))
            metrics.increment("duplicate.moved")


async def main(reference_dir: str, dry_run: bool):
    base_path = os.path.abspath(settings.BASE_PATH)
    reference_dir = os.path.abspath(reference_dir)
    print(f"BASE_PATH={base_path}")

    metrics = SimpleMetricsListener()
    async with AsyncSessionLocal() as session:
        await run(session, base_path, reference_dir, dry_run, metrics)

    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-dir", required=True, help="Directory to check for existing duplicates")
    parser.add_argument("--dry-run", action="store_true", help="Report matches without moving files")
    args = parser.parse_args()
    load_env()
    asyncio.run(main(args.reference_dir, args.dry_run))