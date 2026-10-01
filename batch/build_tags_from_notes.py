import argparse
import asyncio
import uuid
from pathlib import Path

from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import settings, load_env
from metrics.listener import SimpleMetricsListener
from rules.concept_tagger import ConceptTagger
from Storage.db import AsyncSessionLocal
from repository.images import ImagesRepository
from repository.tags import NOTE_TAG_SOURCE, TagsRepository, TagsSaver

_SCRIPT_DIR = Path(__file__).parent


async def _process(incremental: bool) -> None:
    data_dir = settings.get("RULES.TAGGING_DATA_DIR") or str(_SCRIPT_DIR / "data" / "tagging")
    profile = settings.get("GENERAL.TAGGING_PROFILE")
    engine = ConceptTagger.load(data_dir, profile)

    async with AsyncSessionLocal() as session:
        tags_repo = TagsRepository(session)
        images_repo = ImagesRepository(session)

        if not incremental:
            await tags_repo.delete_tags(NOTE_TAG_SOURCE)

        print(f"Tagging notes with profile '{profile}' from {data_dir} ...")
        print(f"Mode: {'incremental' if incremental else 'full'}")

        if incremental:
            notes = await images_repo.get_notes_needing_tags(NOTE_TAG_SOURCE)
            # Tags are rewritten from the current note text, so drop the old ones first; same
            # session as the saver below, so both commit together.
            await tags_repo.delete_tags_for_images(NOTE_TAG_SOURCE, {image_id for image_id, _text in notes})
        else:
            notes = await images_repo.get_all_notes()

        metrics = SimpleMetricsListener()
        async with TagsSaver(session) as tags_saver:
            for image_id, text in notes:
                # Notes carry no language tag, so language=None (script-based fallback), the same
                # convention batch/build_description_note_lemmas.py indexes with.
                result = engine.tag(text, language=None)
                for tag_name, tag_value in result.tags:
                    tags_saver.add_tag(image_id, tag_name, tag_value, NOTE_TAG_SOURCE)
                metrics.increment("notes.processed")
                metrics.add("tags.total", len(result.tags))
        print("Tags:")
        metrics.print()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None, incremental: bool = True) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process(incremental=incremental)
    else:
        async with tracked_run(kind="build_tags_from_notes", trigger=trigger):
            await _process(incremental=incremental)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--incremental", action="store_true",
                        help="Only (re)tag notes with no Note tags yet or edited since their last tagging "
                             "(default: clear all Note tags and reprocess)")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(incremental=args.incremental))
