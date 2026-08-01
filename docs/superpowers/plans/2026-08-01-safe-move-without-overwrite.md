# Safe Move Without Overwrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reusable `move_without_overwrite()` utility that renames with a numeric suffix instead
of silently overwriting a same-named file at the destination, used by `move_flagged.py` (moving
into `excluded/`) and `ingest_hash_dedup.py` (moving ingestion-inbox survivors into `BASE_PATH`).

**Architecture:** `batch/utils/safe_move.py` computes a collision-free destination filename
(truncating the stem upfront to leave room for a suffix, then trying `name.ext`, `name_1.ext`,
`name_2.ext`, ... until one doesn't already exist) and performs the move, returning the filename
actually used. `move_flagged.py`'s per-file loop and `ingest_hash_dedup.py`'s
`register_and_move_to_base_path()` both switch to it; the latter must move before registering
(previously the reverse), since registration needs the final filename.

**Tech Stack:** Python 3.11, pytest + pytest-asyncio, real `tmp_path` filesystem in tests (no
mocking needed for pure filesystem logic).

**Spec:** `docs/superpowers/specs/2026-08-01-safe-move-without-overwrite-design.md`

## Global Constraints

- The stem is truncated **before** any collision check (not only when a suffix would push it over
  the limit) — reserve fixed headroom so appending `_N` can never make an already-near-the-limit
  name unwritable.
- `MAX_FILENAME_LENGTH = 255`, `_SUFFIX_RESERVE = 6` — exact values.
- Directory creation stays the caller's responsibility — the utility never calls `os.makedirs`.
- Out of scope: `batch/move_reference_duplicates.py`, `Backend/app/services/image_store.py`, the
  in-batch/cross-corpus duplicate-holding moves inside `ingest_hash_dedup.py` itself,
  `batch/experimental/*` — none of these change in this plan.

---

### Task 1: `batch/utils/safe_move.py`

**Files:**
- Create: `batch/utils/safe_move.py`
- Test: `batch/tests/test_safe_move.py`

**Interfaces:**
- Produces: `move_without_overwrite(src_path: str, dest_dir: str) -> str`, `MAX_FILENAME_LENGTH`
  (module-level constant, `255`). Tasks 2 and 3 both import `move_without_overwrite`; this task's
  own tests additionally import the private `_SUFFIX_RESERVE` constant for exact assertions (white
  -box testing of an internal implementation detail, not part of the module's public contract).

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_safe_move.py`:

```python
"""
Unit tests for batch/utils/safe_move.py -- collision-safe file moving (renames with a
numeric suffix instead of silently overwriting), matching test_file_hash.py's
flat-function style. Real tmp_path files throughout -- no mocking needed for pure
filesystem logic.

The two truncation tests monkeypatch MAX_FILENAME_LENGTH down to a small value rather
than creating a real ~255-character filename: Windows' classic 260-character MAX_PATH
applies to the full path, not just the filename component, and pytest's tmp_path is
already a fairly deep path, so a genuinely long filename risks failing at file-creation
time in the test itself (unrelated to the truncation logic under test) rather than
exercising it.
"""
from batch.utils.safe_move import MAX_FILENAME_LENGTH, move_without_overwrite


def _write(path, content: bytes = b"x") -> str:
    path.write_bytes(content)
    return str(path)


def test_moves_as_is_when_no_collision(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = _write(src_dir / "a.jpg")

    result = move_without_overwrite(src, str(dest_dir))

    assert result == "a.jpg"
    assert (dest_dir / "a.jpg").exists()
    assert not (src_dir / "a.jpg").exists()


def test_renames_with_suffix_on_single_collision(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = _write(src_dir / "a.jpg", b"new")
    _write(dest_dir / "a.jpg", b"existing")

    result = move_without_overwrite(src, str(dest_dir))

    assert result == "a_1.jpg"
    assert (dest_dir / "a_1.jpg").read_bytes() == b"new"
    assert (dest_dir / "a.jpg").read_bytes() == b"existing"  # untouched, not overwritten


def test_increments_suffix_past_multiple_collisions(tmp_path):
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    src = _write(src_dir / "a.jpg", b"newest")
    _write(dest_dir / "a.jpg", b"first")
    _write(dest_dir / "a_1.jpg", b"second")

    result = move_without_overwrite(src, str(dest_dir))

    assert result == "a_2.jpg"
    assert (dest_dir / "a_2.jpg").read_bytes() == b"newest"


def test_truncates_long_filename_even_without_collision(tmp_path, monkeypatch):
    import batch.utils.safe_move as module
    monkeypatch.setattr(module, "MAX_FILENAME_LENGTH", 20)
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    long_name = "a" * 30 + ".jpg"  # comfortably exceeds the patched 20-char limit
    src = _write(src_dir / long_name)

    result = module.move_without_overwrite(src, str(dest_dir))

    assert len(result) <= 20
    assert result.endswith(".jpg")
    assert (dest_dir / result).exists()


def test_truncated_name_still_gets_a_suffix_on_collision(tmp_path, monkeypatch):
    import batch.utils.safe_move as module
    monkeypatch.setattr(module, "MAX_FILENAME_LENGTH", 20)
    src_dir = tmp_path / "src"
    dest_dir = tmp_path / "dest"
    src_dir.mkdir()
    dest_dir.mkdir()
    ext = ".jpg"
    max_stem_length = 20 - len(ext) - module._SUFFIX_RESERVE
    long_stem = "c" * (max_stem_length + 10)
    src = _write(src_dir / f"{long_stem}{ext}", b"new")
    truncated_stem = long_stem[:max_stem_length]
    _write(dest_dir / f"{truncated_stem}{ext}", b"existing")

    result = module.move_without_overwrite(src, str(dest_dir))

    assert result == f"{truncated_stem}_1{ext}"
    assert (dest_dir / result).read_bytes() == b"new"
    assert (dest_dir / f"{truncated_stem}{ext}").read_bytes() == b"existing"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_safe_move.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch.utils.safe_move'`.

- [ ] **Step 3: Implement `batch/utils/safe_move.py`**

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_safe_move.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add batch/utils/safe_move.py batch/tests/test_safe_move.py
git commit -m "feat: add move_without_overwrite() collision-safe file-move utility"
```

---

### Task 2: Wire into `move_flagged.py`

**Files:**
- Modify: `batch/move_flagged.py`
- Modify: `batch/tests/test_move_flagged.py`

**Interfaces:**
- Consumes: `move_without_overwrite(src_path, dest_dir) -> str` (Task 1).
- `run()`'s signature and return type (`SimpleMetricsListener`) are unchanged — only its internal
  move call changes.

- [ ] **Step 1: Update `batch/move_flagged.py`**

Remove `import shutil` (line 4) — it becomes unused once this is the file's only `shutil.move`
call site, and that call now lives inside `batch/utils/safe_move.py` instead. Add
`from batch.utils.safe_move import move_without_overwrite` after the `batch.run_tracking` import
(alphabetically before `config.settings`).

Replace the per-file loop body in `run()`:

```python
    for (filename, ) in images:
        path_from = os.path.join(base_path, filename)
        try:
            print(f"Moving {filename} from {path_from} to {flagged_path}")
            final_filename = move_without_overwrite(path_from, flagged_path)
            if final_filename != filename:
                print(f"Renamed to avoid overwrite: {filename} -> {final_filename}")
                metrics.increment("renamed_to_avoid_overwrite")
            metrics.increment("moved")
        except FileNotFoundError as e:
            print(f"Skipping {filename}: not found ({e})")
            metrics.increment("error.file_not_found")
        except Exception as e:
            print(f"Skipping {filename}: move failed ({e})")
            metrics.increment("error.move_failed")
```

(`path_to` is removed — `move_without_overwrite` computes the destination path itself from
`flagged_path` and the source's basename.)

- [ ] **Step 2: Rewrite the test that monkeypatches `shutil.move` directly**

`test_other_move_error_is_counted_and_does_not_abort` currently does
`monkeypatch.setattr(module.shutil, "move", fake_move)` against `batch.move_flagged`'s own
`shutil` import — which no longer exists after Step 1. In `batch/tests/test_move_flagged.py`,
replace that test's body to patch `shutil.move` one level down, inside `batch.utils.safe_move`
(the module that now actually calls it):

```python
    @pytest.mark.asyncio
    async def test_other_move_error_is_counted_and_does_not_abort(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.jpg").write_bytes(b"x")
        session = _mock_session(["a.jpg", "b.jpg"])

        import batch.utils.safe_move as safe_move_module
        real_move = safe_move_module.shutil.move

        def fake_move(src, dst):
            if str(src).endswith("a.jpg"):
                raise PermissionError("locked")
            return real_move(src, dst)

        monkeypatch.setattr(safe_move_module.shutil, "move", fake_move)

        metrics = await run(session, str(tmp_path))

        assert metrics.counters_dict() == {"error.move_failed": 1, "moved": 1}
        assert (tmp_path / "excluded" / "b.jpg").exists()
        assert not (tmp_path / "excluded" / "a.jpg").exists()
```

- [ ] **Step 3: Add a new test for the rename-on-collision path**

Add to `TestRun` in `batch/tests/test_move_flagged.py`:

```python
    @pytest.mark.asyncio
    async def test_renames_on_overwrite_collision_and_counts_it(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"new-content")
        excluded_dir = tmp_path / "excluded"
        excluded_dir.mkdir()
        (excluded_dir / "a.jpg").write_bytes(b"already-excluded-content")
        session = _mock_session(["a.jpg"])

        metrics = await run(session, str(tmp_path))

        assert metrics.counters_dict() == {"moved": 1, "renamed_to_avoid_overwrite": 1}
        assert (excluded_dir / "a_1.jpg").read_bytes() == b"new-content"
        assert (excluded_dir / "a.jpg").read_bytes() == b"already-excluded-content"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_move_flagged.py -v`
Expected: all PASS (the rewritten test and the new test both green; the other pre-existing tests in
this file untouched and still passing).

- [ ] **Step 5: Run the full `batch/tests/` root**

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/ -v`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add batch/move_flagged.py batch/tests/test_move_flagged.py
git commit -m "feat: move_flagged renames instead of overwriting on filename collision"
```

---

### Task 3: Wire into `ingest_hash_dedup.py`

**Files:**
- Modify: `batch/ingest_hash_dedup.py`
- Modify: `tests/integration/test_ingest_hash_dedup.py`

**Interfaces:**
- Consumes: `move_without_overwrite(src_path, dest_dir) -> str` (Task 1).
- `register_and_move_to_base_path()`'s signature and return type (`list` of registered image ids)
  are unchanged — only its internal ordering (move before register, not after) and move call
  change.

- [ ] **Step 1: Write the failing integration test**

`tests/integration/test_ingest_hash_dedup.py` needs a live PostgreSQL test DB — see this file's
own module docstring and CLAUDE.md's integration-test gotchas (run with
`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`).

Add this test immediately after `test_register_and_move_creates_pending_images_in_base_path`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_register_and_move_renames_on_filename_collision(tmp_path, db_session):
    source = tmp_path / "source"
    base = tmp_path / "base"
    source.mkdir()
    base.mkdir()
    _write(source, "new.jpg", b"new-bytes")
    _write(base, "new.jpg", b"different-existing-bytes")  # collision: base already has this name

    runs_repo = BatchRunRepository(db_session)
    batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")

    ids = await register_and_move_to_base_path(
        db_session, str(source), str(base), {"new.jpg": "abc123"}, batch_id
    )

    assert len(ids) == 1
    image = await db_session.get(Image, ids[0])
    assert image.filename == "new_1.jpg"
    assert os.path.exists(base / "new_1.jpg")
    assert not os.path.exists(source / "new.jpg")
    # the pre-existing file at base/new.jpg must be untouched, not overwritten
    assert (base / "new.jpg").read_bytes() == b"different-existing-bytes"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_ingest_hash_dedup.py::test_register_and_move_renames_on_filename_collision -v`
Expected: FAIL — the current implementation overwrites `base/new.jpg` and registers the DB row with
the original (unrenamed) `filename`, so `image.filename == "new_1.jpg"` fails.

- [ ] **Step 3: Update `register_and_move_to_base_path()` in `batch/ingest_hash_dedup.py`**

Add `from batch.utils.safe_move import move_without_overwrite` after the existing
`from batch.utils.file_hash import sha256_file` import (alphabetically before `config.settings`).

Replace the function body:

```python
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
```

(This moves the file before registering it, reversed from today's register-then-move order —
necessary since the final filename isn't known until after the move is attempted. The module's own
docstring already documents that a crash between the two steps isn't rolled back either way.)

- [ ] **Step 4: Run the new test to verify it passes**

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_ingest_hash_dedup.py -v`
Expected: all PASS, including the new test and the pre-existing
`test_register_and_move_creates_pending_images_in_base_path`/`test_run_end_to_end_stats_and_final_state`
(neither of those two creates a pre-existing colliding file, so their behavior/assertions are
unaffected by the reordering).

- [ ] **Step 5: Run the full `batch/tests/` root and the full `tests/integration/` root**

Per this project's own testing gotcha (a change to a script with an existing dedicated integration
test file needs the whole `tests/integration/` root run, not just that one file, before merging):

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/ -v`
Expected: all PASS.

Run: `cd H:\workspace_sandbox\memes\.claude\worktrees\safe-move-without-overwrite && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -v`
Expected: all PASS, no regressions elsewhere.

- [ ] **Step 6: Commit**

```bash
git add batch/ingest_hash_dedup.py tests/integration/test_ingest_hash_dedup.py
git commit -m "feat: ingest_hash_dedup renames instead of overwriting on inbox/base_path filename collision"
```

## Self-Review Notes

- **Spec coverage:** the utility itself with truncate-before-collision-check ordering and the
  exact constants (Task 1), `move_flagged.py`'s switch plus its now-unused `shutil` import removal
  plus the new `renamed_to_avoid_overwrite` metric (Task 2), `ingest_hash_dedup.py`'s
  move-then-register reordering (Task 3) — every part of the spec has a corresponding task.
- **Type consistency:** `move_without_overwrite`'s signature and return type are used identically
  in Task 2 and Task 3's call sites and in Task 1's own tests.
- **Existing-test audit, done during this plan's own writing (not left for the review loop):**
  found and fully specified the exact rewrite `test_other_move_error_is_counted_and_does_not_abort`
  needs (it monkeypatches `module.shutil.move`, which stops existing once `move_flagged.py` drops
  its own `shutil` import) — Task 2, Step 2. Found and fully specified the new collision-scenario
  integration test `tests/integration/test_ingest_hash_dedup.py` needs, since its existing coverage
  is happy-path only — Task 3, Step 1.
- **Windows filename-length testing risk, addressed during this plan's own writing:** the spec's
  "long filename" test scenarios could not safely use a real ~255-character filename, since
  Windows' MAX_PATH (260, unless long-path support is separately enabled) applies to the full path
  and pytest's `tmp_path` fixture already produces a non-trivial path depth — a genuinely long
  filename risked failing at file-creation time in the test's own setup, for a reason unrelated to
  the truncation logic under test. Task 1's two truncation tests monkeypatch
  `MAX_FILENAME_LENGTH` down to a small value (20) instead, exercising the identical logic path
  safely and portably.
