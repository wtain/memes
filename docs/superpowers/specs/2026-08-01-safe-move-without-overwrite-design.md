# Safe Move Without Overwrite — Design

Status: approved

**Date:** 2026-08-01.

A reusable utility that moves a file into a destination directory without silently
overwriting a same-named file already there, renaming with a numeric suffix instead — used by
`batch/move_flagged.py` and `batch/ingest_hash_dedup.py`, the two places files move between
directories that can plausibly share filenames (`excluded/` and the ingestion inbox → `BASE_PATH`,
respectively).

---

## Motivation

`shutil.move(src, dst)` silently overwrites whatever already exists at `dst`. Two call sites in
this codebase move files into a directory that can already contain a same-named file:

- `move_flagged.py` moves flagged images into `BASE_PATH/excluded/` — a second image that happens
  to share a filename with something already excluded currently clobbers it.
- `ingest_hash_dedup.py`'s `register_and_move_to_base_path()` moves survivors from the ingestion
  inbox (`PATH_INGESTION_SOURCE`) into `BASE_PATH` — the inbox and the active library can contain
  files with the same name (different content, since identical-content files were already caught
  by hash-based dedup earlier in that same script), and this move currently clobbers silently too.

## Scope

**In scope:** one new utility function, and updating these two existing call sites to use it.

**Out of scope:** the other `shutil.move` call sites found in the codebase
(`batch/move_reference_duplicates.py`, `Backend/app/services/image_store.py`, the in-batch/
cross-corpus duplicate-holding moves inside `ingest_hash_dedup.py` itself, `batch/experimental/*`)
— none of those were named as needing this, and extending further is a separate decision for
later, not bundled into this change.

## Design

### `batch/utils/safe_move.py`

Matches this package's existing convention (`batch/utils/file_hash.py`): plain functions, no
class, module-level constants for tunables.

```python
import os
import shutil

MAX_FILENAME_LENGTH = 255  # conservative common denominator across NTFS/ext4
_SUFFIX_RESERVE = 6        # room for "_" + up to 4 digits, plus a little headroom


def move_without_overwrite(src_path: str, dest_dir: str) -> str:
    """Move src_path into dest_dir. If a file with the same name already exists there,
    renames with a numeric suffix (name_1.ext, name_2.ext, ...) instead of silently
    overwriting it. Returns the actual filename used at the destination -- callers that
    register this filename elsewhere (e.g. a DB row) must use the returned value, not
    the original.

    The stem is truncated up front, before any collision check, to leave room for a
    numeric suffix -- an already-near-the-filesystem-limit name would otherwise become
    unwritable the moment a suffix is appended. Short names are unaffected (a no-op
    slice).
    """
    filename = os.path.basename(src_path)
    stem, ext = os.path.splitext(filename)
    max_stem_length = max(1, MAX_FILENAME_LENGTH - len(ext) - _SUFFIX_RESERVE)
    stem = stem[:max_stem_length]

    candidate = f"{stem}{ext}"
    counter = 0
    while os.path.exists(os.path.join(dest_dir, candidate)):
        counter += 1
        candidate = f"{stem}_{counter}{ext}"

    shutil.move(src_path, os.path.join(dest_dir, candidate))
    return candidate
```

Directory creation (`os.makedirs(dest_dir, exist_ok=True)`) stays the caller's responsibility —
both existing call sites already do this themselves, once, outside their per-file loops (or with
`exist_ok=True` making a redundant call harmless); the utility's only job is collision-safe moving.

### `move_flagged.py`

Replace the direct `shutil.move(path_from, path_to)` in the per-file loop with:

```python
final_filename = move_without_overwrite(path_from, flagged_path)
if final_filename != filename:
    print(f"Renamed to avoid overwrite: {filename} -> {final_filename}")
    metrics.increment("renamed_to_avoid_overwrite")
metrics.increment("moved")
```

`path_to` (previously `os.path.join(flagged_path, filename)`) is no longer needed — the utility
computes the destination path itself from `flagged_path` and the source's basename. The existing
`FileNotFoundError`/generic-`Exception` handling around this call is unchanged; a source file that
doesn't exist still raises `FileNotFoundError` from inside `move_without_overwrite`'s eventual
`shutil.move` call, caught the same way as today. `move_flagged.py`'s own `import shutil` becomes
unused once this is the only `shutil.move` call in the file — remove it.

### `ingest_hash_dedup.py`

`register_and_move_to_base_path()` currently registers the DB row *before* moving the file, using
the same filename for both. Since the utility only reveals the final filename after attempting the
move, this reorders to move-first-then-register:

```python
async def register_and_move_to_base_path(
    session, source_path: str, base_path: str, survivors: dict[str, str], batch_id
) -> list:
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
```

This is a small, deliberate behavior change (move now happens before the DB write, not after) —
acceptable given this module's own docstring already documents that it doesn't guarantee atomicity
across a crash between the two steps, in either order.

### Testing

`batch/tests/test_safe_move.py` (new, matching `test_file_hash.py`'s flat-function style, real
`tmp_path` files):

- No collision: file moves as-is, returns the original filename.
- One collision: existing file at the destination causes a `_1` suffix.
- Multiple collisions: `_1` also already taken → `_2`, and so on.
- A filename long enough to need truncation, with no collision — confirms truncation happens
  unconditionally, not only when a collision is found.
- A filename long enough to need truncation, *and* a collision after truncation — confirms the
  suffix loop still finds a free name against the truncated stem.

Existing tests that need auditing (both already exist and must still pass, updated for the new
call shape where the assertions require it):

- `batch/tests/test_move_flagged.py` — the existing tests use real `tmp_path` files and assert on
  filesystem state; none currently create a pre-existing file at the destination, so their
  happy-path assertions should be unaffected by switching to the new utility, but must be
  re-verified once the utility is wired in. One existing test,
  `test_other_move_error_is_counted_and_does_not_abort`, simulates a move failure by monkeypatching
  `module.shutil.move` directly — since `move_flagged.py` will no longer import `shutil` at all
  (see above), this test must be rewritten to inject the failure a different way (e.g.
  monkeypatching `batch.utils.safe_move.shutil.move`, the actual `shutil.move` call now one level
  down inside the utility) rather than deleted or left monkeypatching a name that no longer exists.
- `tests/integration/test_ingest_hash_dedup.py`'s
  `test_register_and_move_creates_pending_images_in_base_path` (and its sibling cross-corpus test)
  — covers the happy path only today; must still pass with the reordered
  move-then-register logic, and a new collision-scenario case should be added there (a file
  already present in `base_path` with the same name as a survivor) to exercise the DB row actually
  getting the renamed filename.

## Rollout

1. Add `batch/utils/safe_move.py` + `batch/tests/test_safe_move.py`.
2. Update `move_flagged.py` to use it; audit/update `batch/tests/test_move_flagged.py`.
3. Update `ingest_hash_dedup.py`'s `register_and_move_to_base_path()` to use it; audit/update
   `tests/integration/test_ingest_hash_dedup.py`, adding the new collision-scenario test.
