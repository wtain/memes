"""
Shared per-image orchestration for both batch/ingest_validate_formats.py and
batch/fix_image_formats.py: calls fix_image_file() and persists whatever it reports,
isolating one image's failure from the rest of the run. Deliberately not part of
batch/utils/image_format_fix.py -- that module stays pure file-level logic with no DB
access; this one owns the DB side.
"""
from batch.utils.image_format_fix import fix_image_file
from metrics.listener import SimpleMetricsListener
from repository.image_extras import ImageExtrasRepository
from repository.images import ImagesRepository


async def apply_format_fix(
    images_repo: ImagesRepository,
    extras_repo: ImageExtrasRepository,
    metrics: SimpleMetricsListener,
    base_path: str,
    image_id,
    filename: str,
) -> None:
    """Fixes one image's file and persists the result. Only fix_image_file() -- pure
    filesystem/Pillow logic, no DB access -- is wrapped in try/except: a failure there
    never touches the DB session, so catching it and moving on to the next image is safe.
    The two persistence calls below are deliberately NOT wrapped: a failure in a DB
    statement aborts the whole Postgres transaction server-side, so catching it and
    continuing to issue more statements on the same session would just cascade-fail every
    remaining image with a misleading "error.fix_failed" instead of surfacing the real
    problem -- a DB-level failure is left to propagate and abort run() normally, exactly
    like every other batch script in this codebase already does."""
    try:
        outcome = fix_image_file(base_path, filename)
    except Exception as e:
        print(f"  error fixing {filename}: {e}")
        metrics.increment("error.fix_failed")
        return

    if outcome.unreadable:
        await extras_repo.set_flagged(image_id, True, remarks="unreadable during format validation")
        metrics.increment("unreadable")
        return

    if not outcome.changed:
        metrics.increment("no_op")
        return

    await images_repo.update_filename_and_hash(
        image_id, outcome.new_filename, content_hash=outcome.new_content_hash,
    )
    if outcome.animated:
        metrics.increment("converted_animated")
    else:
        metrics.increment("converted" if outcome.new_content_hash else "renamed")
