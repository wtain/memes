import argparse
import asyncio
import os

from batch.utils.file_hash import files_are_identical, sha256_file
from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from graph.uf import UnionFind
from metrics.listener import SimpleMetricsListener
from repository.image_extras import ImageExtrasRepository
from repository.images import ImagesRepository


def cluster_already_handled(cluster: list, flags: dict) -> bool:
    """True if any member of this cluster is already flagged -- treat the whole
    cluster as already reviewed by a human, and skip flagging anything else in it."""
    return any(flags.get(mid, False) for mid in cluster)


async def main():
    BASE_PATH = settings.BASE_PATH
    print(f"BASE_PATH={BASE_PATH}")
    base_path = os.path.abspath(BASE_PATH)

    metrics = SimpleMetricsListener()

    # ── Phase 1: load all registered images ─────────────────────────────────
    async with AsyncSessionLocal() as session:
        images_repo = ImagesRepository(session)
        all_images = await images_repo.get_all_images_with_hash()

    print(f"Registered images: {len(all_images)}")

    # ── Phase 2: compute SHA-256 for images without a stored hash ────────────
    # Hashing is CPU/IO-bound; done outside any DB session.
    # image_id → (filename, sha256_hex, created_at)
    hashed: dict = {}
    hash_updates: list = []  # (image_id, hash) to persist

    for image_id, filename, stored_hash, created_at in all_images:
        path = os.path.join(base_path, filename)

        if not os.path.exists(path):
            metrics.increment("skipped.missing_file")
            continue

        if stored_hash:
            content_hash = stored_hash
            metrics.increment("hash.from_cache")
        else:
            try:
                content_hash = sha256_file(path)
                hash_updates.append((image_id, content_hash))
                metrics.increment("hash.computed")
            except OSError as e:
                print(f"  error reading {filename}: {e}")
                metrics.increment("error.read")
                continue

        hashed[image_id] = (filename, content_hash, created_at)

    if hash_updates:
        async with AsyncSessionLocal() as session:
            images_repo = ImagesRepository(session)
            for image_id, content_hash in hash_updates:
                await images_repo.update_content_hash(image_id, content_hash)
            await session.commit()
        print(f"Stored {len(hash_updates)} new content hashes")

    # ── Phase 3: cluster by hash using UnionFind ─────────────────────────────
    hash_to_ids: dict = {}
    for image_id, (_, content_hash, _) in hashed.items():
        hash_to_ids.setdefault(content_hash, []).append(image_id)

    uf = UnionFind()
    for ids in hash_to_ids.values():
        if len(ids) > 1:
            first = ids[0]
            for other in ids[1:]:
                uf.connect(first, other)

    # ── Phase 4: verify content and flag duplicates ──────────────────────────
    async with AsyncSessionLocal() as session:
        extras_repo = ImageExtrasRepository(session)

        clusters = [uf.get_cluster(root) for root in uf.list_clusters()]
        clusters = [c for c in clusters if len(c) >= 2]

        all_member_ids = [mid for cluster in clusters for mid in cluster]
        flags = await extras_repo.get_flags_bulk(all_member_ids)

        for cluster in clusters:
            if cluster_already_handled(cluster, flags):
                names = [hashed[mid][0] for mid in cluster]
                print(f"  cluster {len(cluster)}: already has a flagged member, skipping ({names})")
                metrics.increment("clusters.skipped_already_flagged")
                continue

            paths = [os.path.join(base_path, hashed[mid][0]) for mid in cluster]

            if not files_are_identical(paths):
                # SHA-256 collision is astronomically unlikely; more likely a read error
                names = [hashed[mid][0] for mid in cluster]
                print(f"  WARNING: content mismatch despite identical hash in cluster {names} — skipping")
                metrics.increment("warning.content_mismatch")
                continue

            # Keep the oldest (earliest created_at); flag the rest
            by_age = sorted(cluster, key=lambda mid: hashed[mid][2])
            keeper, *duplicates = by_age

            keeper_name = hashed[keeper][0]
            dup_names = [hashed[d][0] for d in duplicates]
            print(f"  cluster {len(cluster)}: keep={keeper_name}  flag={dup_names}")

            for dup_id in duplicates:
                await extras_repo.set_flagged(dup_id, True)
                metrics.increment("flagged")

            metrics.increment("clusters.found")

        await session.commit()

    print(f"\nTotal registered: {len(all_images)}")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())