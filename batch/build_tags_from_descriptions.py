"""
Tags from Ollama descriptions with ConceptTagger (same vocabulary and profile as OCR tagging),
source "Ollama". Each description is tagged on its own as English and the tags are unioned per image. Incremental mode
selects images with no Ollama tag or a newer description and rewrites their Ollama tags; a full run deletes every
Ollama tag first. Run-tracked (kind build_tags_from_descriptions); admin-triggerable, manual only; needs the tagging
YAML of the environment profile (not available for it). --refresh-stale replaces the stale Ollama tags of recut cuts
(cut_proposed) with tags of their refreshed descriptions.
"""
import argparse
import asyncio
import uuid
from pathlib import Path

from collections import defaultdict

from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.refresh_stale import refresh_stats, reject_conflicts
from config.settings import settings, load_env
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from Storage.models import ImageDescription, ImageStatus, ImageTag
from Storage.stale import delete_stale_rows, images_with_stale_rows, stale_among
from repository.image_descriptions import ImageDescriptionsRepository
from repository.images import ImagesRepository
from repository.tags import DESCRIPTION_TAG_SOURCE, TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent
_SOURCE = DESCRIPTION_TAG_SOURCE
# Descriptions are LLM output in English regardless of the meme's own language.
_DESCRIPTION_LANGUAGE = "en"


async def refresh_stale_description_tags(session, engine) -> dict:
    """Replaces the stale Ollama tags of cut_proposed images with tags of their (refreshed) descriptions; tags are
    computed before anything is deleted, so an engine failure leaves every stale tag in place."""
    selected = await images_with_stale_rows(session, ImageTag, ImageStatus.CUT_PROPOSED, source=_SOURCE)
    blocked = await stale_among(session, ImageDescription, selected)
    ready = [image_id for image_id in selected if image_id not in blocked]
    tags_by_image = defaultdict(set)
    for image_id, text in await ImageDescriptionsRepository(session).get_texts_for_images(ready):
        tags_by_image[image_id].update(engine.tag(text, language=_DESCRIPTION_LANGUAGE).tags)
    try:
        await delete_stale_rows(session, ImageTag, ready, source=_SOURCE)
        for image_id in ready:
            for name, value in sorted(tags_by_image.get(image_id, ())):
                session.add(ImageTag(image_id=image_id, key=name, value=value, source=_SOURCE))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return refresh_stats(selected=len(selected), refreshed=len(ready), skipped_upstream_stale=len(blocked))


async def _process(incremental: bool, refresh_stale: bool = False) -> dict | None:
    data_dir = settings.get("RULES.TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    engine = ConceptTagger.load(data_dir, profile)

    if refresh_stale:
        async with AsyncSessionLocal() as session:
            stats = await refresh_stale_description_tags(session, engine)
        print(f"Refresh stale: {stats}")
        return stats

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await tags_repo.delete_tags(_SOURCE)

        total_images = await images_repo.get_total_images()
        print(f"Total images: {total_images}")
        print(f"Tagging with profile '{profile}' from {data_dir} ...")
        print(f"Mode: {'incremental' if incremental else 'full'}")

        if incremental:
            images_and_texts_results = await images_repo.get_images_and_descriptions_needing_tags(_SOURCE)
            # Tags are rewritten from the image's whole description set, so drop the old ones
            # first; same session as the saver below, so both commit together.
            await tags_repo.delete_tags_for_images(
                _SOURCE, {image_id for _filename, image_id, _text in images_and_texts_results}
            )
        else:
            images_and_texts_results = await images_repo.get_images_and_descriptions()

        metrics = SimpleMetricsListener()
        async with TagsSaver(session) as tags_saver:
            # One image has one description per prompt. Each is tagged on its own (so a tag must
            # reach its threshold within a single description) and the results are unioned per
            # image; TagsSaver dedups identical (key, value) pairs.
            for _filename, image_id, text in images_and_texts_results:
                result = engine.tag(text, language=_DESCRIPTION_LANGUAGE)
                for tag_name, tag_value in result.tags:
                    tags_saver.add_tag(image_id, tag_name, tag_value, _SOURCE)
                metrics.increment("descriptions.processed")
                metrics.add("tags.total", len(result.tags))
        print("Tags:")
        metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True,
               refresh_stale: bool = False) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            stats = await _process(incremental=incremental, refresh_stale=refresh_stale)
            if refresh_stale and stats:
                await record_stats(run_id, stats)
    else:
        async with tracked_run(kind="build_tags_from_descriptions", trigger=trigger) as new_run_id:
            stats = await _process(incremental=incremental, refresh_stale=refresh_stale)
            if refresh_stale and stats:
                await record_stats(new_run_id, stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None,
                        help="Environment to load config/secrets for (falls back to APP_ENV)")
    parser.add_argument("--incremental", action="store_true",
                        help="Only process images that have no Ollama tags yet (default: clear all and reprocess)")
    parser.add_argument("--refresh-stale", action="store_true",
                        help="Replace the stale Ollama tags of cut_proposed images (recut) with tags of their "
                             "refreshed descriptions")
    args = parser.parse_args()
    reject_conflicts(parser, args.refresh_stale, incremental=args.incremental)
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental, refresh_stale=args.refresh_stale))
