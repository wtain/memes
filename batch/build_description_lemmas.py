import argparse
import asyncio
import uuid

from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from repository.description_lemmas import DescriptionLemmasRepository, DescriptionLemmasSaver
from rules.normalize import make_morph, normalize
from Storage.db import AsyncSessionLocal

# Ollama descriptions are English LLM output regardless of the meme's own language, so they are
# indexed as "en": words are stemmed at index time exactly like en-tagged OCR rows, which is what
# lets repository/ocr_lemmas.py's _stem_lemma_ids fallback find them.
_DESCRIPTION_LANGUAGE = "en"


async def run(session, morph, min_word_length: int) -> None:
    repo = DescriptionLemmasRepository(session)
    rows = await repo.get_descriptions_needing_lemmas()
    print(f"Found {len(rows)} description(s) needing lemma indexing")

    tracker = ProgressTracker(len(rows), report_every=100, report_interval_secs=10)

    async with DescriptionLemmasSaver(session) as saver:
        for description_id, text in rows:
            lemma_set = normalize(
                text, morph, min_length=min_word_length,
                language=_DESCRIPTION_LANGUAGE, keep_digit_tokens=True,
            )
            await saver.add_lemmas(description_id, lemma_set)
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
        async with tracked_run(kind="build_description_lemmas", trigger=trigger):
            await _process()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
