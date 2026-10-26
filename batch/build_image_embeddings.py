"""
CLIP image embeddings (512-dim) into the embeddings table. --incremental embeds only images with no
embedding; without it the in-scope embeddings are deleted and rebuilt. --status {pending,active,cut_proposed,all} selects
the images (ingestion runs it with pending; recut cuts: --status cut_proposed --incremental). Run-tracked (kind build_image_embeddings); main() defaults to incremental
and active so a scheduled or admin run never wipes embeddings. Admin-triggerable, manual only.
"""
import argparse
import asyncio
import os
import uuid

from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count

from ai.clip import ClipModel
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from embeddingutils.image import load_image
from metrics.listener import SimpleMetricsListener
from Storage.db import AsyncSessionLocal
from Storage.models import Embedding

from Storage.models import Image as Img


def _status_filter(target_status: str):
    return () if target_status == "all" else (Img.status == target_status,)


async def select_images_to_embed(session, incremental: bool, target_status: str):
    """(filename, id) rows of the images to embed: every image of the status, or with `incremental` only those that
    have no vector yet."""
    stmt = select(Img.filename, Img.id).where(*_status_filter(target_status))
    if incremental:
        has_embedding = select(Embedding.image_id).distinct().scalar_subquery()
        stmt = stmt.where(Img.id.not_in(has_embedding))
    return (await session.execute(stmt)).all()


async def _process(incremental: bool, target_status: str = "active") -> None:

    status_filter = _status_filter(target_status)

    async with AsyncSessionLocal() as session:
        if not incremental:
            print(f"Deleting embeddings (status={target_status})...")
            in_scope_ids = select(Img.id).where(*status_filter).scalar_subquery()
            await session.execute(
                delete(Embedding).where(Embedding.image_id.in_(in_scope_ids))
            )
            await session.commit()
            print("Done")

        total_images = (await session.execute(
            select(count(Img.id)).where(*status_filter)
        )).scalar_one()
        print(f"Total images (status={target_status}): {total_images}")

        rows = await select_images_to_embed(session, incremental, target_status)
        print(f"Found {len(rows)} image(s) needing embeddings")

        clip_model = ClipModel()

        BASE_PATH = settings.BASE_PATH
        print(f"BASE_PATH={BASE_PATH}")
        base_path = os.path.abspath(BASE_PATH)

        batch_size = settings.GENERAL.BATCH_SIZE
        metrics = SimpleMetricsListener()
        tracker = ProgressTracker(total=len(rows), report_every=settings.GENERAL.PROGRESS_EVERY)

        print(f"Processing on {clip_model.device}")
        for i, (filename, image_id) in enumerate(rows):
            path = os.path.join(base_path, filename)
            if os.path.isdir(path):
                metrics.increment("skipped.directory")
                tracker.skip()
            elif not os.path.exists(path):
                metrics.increment("skipped.missing_file")
                tracker.skip()
            else:
                try:
                    image = load_image(path)
                    vector = clip_model.embed_image(image)
                    session.add(Embedding(image_id=image_id, embedding=vector.tolist()))
                    metrics.increment("embedded")
                except Exception as e:
                    print(f"Can't read {path}: {e}")
                    metrics.increment("error.embed_failed")
                tracker.mark_done()

            if (i + 1) % batch_size == 0:
                await session.commit()

        print("Committing...")
        await session.commit()
        print("Done")

        tracker.summary()
        metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None,
               incremental: bool = True, target_status: str = "active") -> None:
    # incremental defaults to True: a scheduled or admin-triggered run (which cannot pass flags) must
    # never silently delete and rebuild every embedding. The CLI passes its own --incremental value.
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental, target_status)
    else:
        async with tracked_run(kind="build_image_embeddings", trigger=trigger):
            await _process(incremental, target_status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only embed images that have no embedding yet (default: clear all and reprocess)")
    parser.add_argument("--status", choices=["pending", "active", "cut_proposed", "all"], default="active",
                        help="Only embed images with this registration status (default: active). "
                             "Ingestion's own duplicate-review stage calls this with --status pending.")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental, target_status=args.status))
