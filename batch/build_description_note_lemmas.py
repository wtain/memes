import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from repository.description_note_lemmas import DescriptionNoteLemmasRepository, DescriptionNoteLemmasSaver
from rules.normalize import make_morph, normalize
from Storage.db import AsyncSessionLocal


async def run(session, morph, min_word_length: int) -> None:
    lemmas_repo = DescriptionNoteLemmasRepository(session)
    rows = await lemmas_repo.get_notes_needing_lemmas()
    print(f"Found {len(rows)} description note(s) needing lemma indexing")

    tracker = ProgressTracker(len(rows), report_every=100, report_interval_secs=10)

    async with DescriptionNoteLemmasSaver(session) as saver:
        for image_id, text, updated_at in rows:
            # language=None: no per-note language tag exists, so this
            # matches matching_image_ids' own query-time convention
            # (script-based pymorphy3 fallback). Means the note-lemma
            # index is never pre-stemmed for English -- see the comment
            # above _stem_lemma_ids in repository/ocr_lemmas.py.
            lemma_set = normalize(
                text, morph, min_length=min_word_length, language=None, keep_digit_tokens=True
            )
            await saver.replace_lemmas(image_id, lemma_set)
            await lemmas_repo.mark_lemmas_built(image_id, updated_at)
            tracker.mark_done()

    tracker.summary()


async def _process() -> None:
    morph = make_morph()
    min_word_length = settings.BOW.MIN_WORD_LENGTH
    async with AsyncSessionLocal() as session:
        await run(session, morph, min_word_length)


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _process()
    else:
        async with tracked_run(kind="build_description_note_lemmas", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
