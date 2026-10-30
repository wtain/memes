# R6 Box Adjustment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner correct the detector's rectangle on `/admin/recut`: the cut is rebuilt from the new box inside the API request, and every approval copies the source file to `BASE_PATH/pre-cuts/`.

**Architecture:** One Alembic revision adds four nullable `detected_crop_*` columns to `image_cuts`. A new `RecutAdjustService` locks the proposal row, crops the source on a worker thread with the existing `crop_to_file`, inserts a new `cut_proposed` image, copies the derived data with the existing `CutCopier`, repoints `cut_image_id`, deletes the old cut image and records an `adjusted` event; file removal is tied to the transaction with session hooks. `POST /api/recut/proposals/{id}/adjust` returns the updated detail. The review page gets an edit mode (draggable box with eight handles, numeric fields, Apply / Cancel / Reset).

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, Pillow (new in the backend image), React + Vite + vitest, shared JSON schemas.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-30-r6-box-adjustment-design.md`

## Global Constraints

- Repositories never commit; `get_async_db` commits after the response and rolls back on an exception.
- Every precondition is checked before the first write (a refusal leaves nothing half applied).
- Box: integers, `x >= 0`, `y >= 0`, each side at least 16 px, inside the source, different from the current box (422 otherwise).
- 409 codes: `already_decided`, `cut_missing`, `source_missing`, `source_not_active`, `source_flagged`, `source_changed`, `source_unreadable`; 500 detail `write_error`.
- The `image_cuts` row, its id and the state `proposed` are kept; `confidence` is untouched; `detected_crop_*` is written only by the first adjustment (all four NULL before it).
- The old cut file is removed only after the commit; the new cut file is removed if the transaction rolls back; removal failures are logged and swallowed.
- Approve copies the source file to `BASE_PATH/pre-cuts/<source filename>` after the commit with `shutil.copy2`, never overwrites, swallows and logs failures; reject copies nothing.
- `adjusted` is not exclusive with `approved`/`rejected`; metric `adjusted_share = adjusted / proposed`.
- Pillow is added to `Backend/requirements-backend.txt` at `12.0.0` (the version in the root `requirements.txt`).
- Python 3.11 venv: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe` (call it by absolute path from the worktree). Test DB: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`. Never use the live `DATABASE_URL`; never run a live migration.
- Test roots run separately: `tests/integration/`, `Backend/` (from its dir), `batch/tests/`, `tests/evaluation/`, `tests/rules/`, `tests/docs/`, vitest. The shared test DB can produce random deadlocks when another process uses it: rerun, do not "fix".
- Known unrelated failure: `Backend/tests/test_batch_subprocess.py::...::test_forces_utf8_output_encoding_in_child_env`.
- Many files are CRLF: edit with the Edit tool (or a script that preserves line endings); do not round-trip repo text through PowerShell `Get/Set-Content`.
- Git: plain commands only (no `git -C`, no xargs). Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Nothing is pushed.

## Review Focus

- The source file was replaced or edited after detection: adjusting returns 409 `source_changed` and leaves no new file and no new row (Task 7).
- A box that exceeds the source, is tiny, or equals the current box: 422 and no file is left in `BASE_PATH` (Task 7).
- Adjusting a PNG and a JPEG keeps the format and the exact pixel size of the box (Task 7).
- Adjusting a proposal that was already approved or rejected: 409 `already_decided`, no new file (Task 7).
- A database failure after the new file is written leaves no orphan file; the old file survives a rollback (Tasks 5 and 7).
- Approving when the source file is gone or `pre-cuts/<name>` already exists: the approval still succeeds and nothing is overwritten (Task 6).
- Typing in a numeric field never approves, rejects or navigates (Task 12).

---

### Task 1: Migration and model columns

**Files:**
- Create: `Storage/alembic/versions/e9c4a7b3d812_add_image_cuts_detected_box.py`
- Modify: `Storage/models.py` (class `ImageCut`, after `failure_reason`)
- Test: `tests/integration/test_image_cuts_migration.py` (add tests)

**Interfaces:**
- Produces: `ImageCut.detected_crop_x/y/w/h` (nullable `Integer`); revision `e9c4a7b3d812`, `down_revision = 'c8e2f5a1d093'` (verify this is still the only head with `alembic heads`-equivalent: the one revision no other revision lists as `down_revision`).

- [ ] **Step 1: Write the failing tests.** In `tests/integration/test_image_cuts_migration.py`, follow the existing `_load_r3a` pattern: add `R6_MIGRATION` pointing to `e9c4a7b3d812_add_image_cuts_detected_box.py`, a `_load_r6()` loader, and:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_r6_migration_adds_and_drops_the_four_detected_box_columns(db_engine):
    names = ("detected_crop_x", "detected_crop_y", "detected_crop_w", "detected_crop_h")
    migration = _load_r6()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(lambda c: _run(c, migration.downgrade))
            columns = await connection.run_sync(lambda c: {col["name"] for col in inspect(c).get_columns("image_cuts")})
            assert not set(names) & columns
            await connection.run_sync(lambda c: _run(c, migration.upgrade))
            shape = await connection.run_sync(lambda c: {col["name"]: col["nullable"] for col in inspect(c).get_columns("image_cuts")})
            assert all(shape[name] is True for name in names)
        finally:
            await transaction.rollback()


def test_the_model_declares_the_detected_box_columns():
    from Storage.models import ImageCut
    for name in ("detected_crop_x", "detected_crop_y", "detected_crop_w", "detected_crop_h"):
        assert ImageCut.__table__.c[name].nullable is True
```
Match how the neighbouring R3a migration test drives the connection (copy its structure exactly; the snippet above is the intent).

- [ ] **Step 2: Run to verify failure.** `DATABASE_URL=... H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_image_cuts_migration.py -v` → FAIL (migration file missing / attribute missing).

- [ ] **Step 3: Implement.** Model: after `failure_reason` add

```python
    # The detector's box, stored by the first manual adjustment (R6); NULL means never adjusted. Metadata only.
    detected_crop_x = Column(Integer, nullable=True)
    detected_crop_y = Column(Integer, nullable=True)
    detected_crop_w = Column(Integer, nullable=True)
    detected_crop_h = Column(Integer, nullable=True)
```
Migration (same header style as `d7a3b9e2c451`):

```python
"""add image_cuts.detected_crop_x/y/w/h

Revision ID: e9c4a7b3d812
Revises: c8e2f5a1d093
Create Date: 2026-10-30 10:00:00.000000

Epic screenshot-recut, project R6. Four nullable integers with no default: no table rewrite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e9c4a7b3d812'
down_revision: Union[str, Sequence[str], None] = 'c8e2f5a1d093'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = ("detected_crop_x", "detected_crop_y", "detected_crop_w", "detected_crop_h")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column('image_cuts', sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column('image_cuts', name)
```

- [ ] **Step 4: Run to verify pass.** Same command → PASS; then run all of `tests/integration/test_image_cuts_migration.py` and `tests/integration/test_image_cuts_repository.py`.
- [ ] **Step 5: Commit.** `git add Storage tests/integration/test_image_cuts_migration.py` then `git commit -m "feat(recut): R6 detected box columns" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"`.

---

### Task 2: Backend packaging (Pillow, Dockerfile, .dockerignore, smoke test)

**Files:**
- Modify: `Backend/requirements-backend.txt`, `Dockerfile.backend`, `.dockerignore`
- Test: `tests/docker/test_backend_image_boots.py` (add a test)

**Interfaces:**
- Produces: the backend image can `import batch.utils.recut_cut` and `import repository.cut_copier` (needs Pillow, `batch/utils/__init__.py`, `recut_cut.py`, `file_hash.py`).

- [ ] **Step 1: Write the failing test** (append to `tests/docker/test_backend_image_boots.py`):

```python
def test_image_can_import_the_recut_cutter(built_image):
    result = _run([
        "docker", "run", "--rm", *DUMMY_ENV, built_image,
        "python", "-c",
        "import PIL; from batch.utils.recut_cut import crop_to_file; from repository.cut_copier import CutCopier; "
        "import Backend.app.api.recut",
    ])
    assert result.returncode == 0, (
        f"the image cannot import the recut cutter (Pillow or a batch/utils COPY missing?):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
```
Note: `Backend.app.api.recut` imports the adjust service only after Task 8; the import line is harmless until then. If Docker is unavailable the test skips: say so in the ledger and verify the three COPY lines and the `.dockerignore` exceptions by reading both files.

- [ ] **Step 2: Run to verify failure.** `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/docker/ -v` → FAIL (`No module named 'PIL'`) or SKIP (no daemon).
- [ ] **Step 3: Implement.**
  - `Backend/requirements-backend.txt`: add `pillow==12.0.0` in alphabetical position (keep the file's existing style and casing).
  - `Dockerfile.backend`, after the `batch/registry.py` COPY:
    ```
    # Backend/app/services/recut_adjust_service.py rebuilds a cut inside the request: it needs the cropper
    # (batch/utils/recut_cut.py, which needs file_hash.py) and, through repository/cut_copier.py, the same module again.
    COPY batch/utils/__init__.py ./batch/utils/__init__.py
    COPY batch/utils/recut_cut.py ./batch/utils/recut_cut.py
    COPY batch/utils/file_hash.py ./batch/utils/file_hash.py
    ```
  - `.dockerignore` (after `!batch/registry.py`): `!batch/utils` is not enough with `batch/*`; add
    ```
    batch/utils/*
    !batch/utils/__init__.py
    !batch/utils/recut_cut.py
    !batch/utils/file_hash.py
    ```
    and put `!batch/utils` on its own line before them, mirroring how `!batch/data` / `batch/data/*` / `!batch/data/taxonomy` are written.
- [ ] **Step 4: Verify.** Run `tests/docker/` (PASS or SKIP with the reason). Also `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -c "import PIL; print(PIL.__version__)"` prints a 12.x version.
- [ ] **Step 5: Commit** (`feat(recut): R6 backend image carries Pillow and the cutter`).

---

### Task 3: Schemas, regenerated types, and the read side (`detectedBox`, `adjusted`)

**Files:**
- Create: `shared/schemas/recutadjustrequest.schema.json`
- Modify: `shared/schemas/recutproposaldetail.schema.json`, `shared/schemas/recutstatsband.schema.json`, `Backend/app/repositories/recut_repository.py`
- Regenerate: `Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient` DTOs
- Test: `tests/integration/test_recut_stats.py`, `tests/integration/test_recut_repository.py` (use the file that already tests `RecutRepository.get`; `grep -l "RecutRepository" tests/integration`), `Backend/tests/test_recut_endpoints.py`

**Interfaces:**
- Produces: `RecutRepository.get()` returns `"detectedBox": {"x","y","w","h"} | None`; every band dict from `band_stats()` (and `total`) has `"adjusted": int`; schema `RecutAdjustRequest {x,y,w,h}` (integers; `x`,`y` minimum 0, `w`,`h` minimum 16); generated Python `Backend.app.types.generated.recutadjustrequest.Schema`.

- [ ] **Step 1: Write failing tests.**
  - Repository `get`: a row with all four `detected_crop_*` set returns `detectedBox == {"x":..,"y":..,"w":..,"h":..}`; a row with them NULL returns `detectedBox is None`.
  - `band_stats`: two `proposed` rows in the 0.9-1.0 band, one with `detected_crop_x` set, plus one `approved` adjusted row in the same band → that band has `adjusted == 2`, `total["adjusted"] == 2`, an empty band has `adjusted == 0`.
  - `Backend/tests/test_recut_endpoints.py`: extend `DETAIL` with `"detectedBox": None` and `BAND` with `"adjusted": 0` (the existing tests then pass only once the schemas know the fields).
- [ ] **Step 2: Run to verify failure** (integration file, then `cd Backend && pytest tests/test_recut_endpoints.py`): FAIL (missing keys / schema rejects extra fields).
- [ ] **Step 3: Implement.**
  - `recutproposaldetail.schema.json`: add property `"detectedBox": { "oneOf": [ { "type": "null" }, { "type": "object", "additionalProperties": false, "properties": { "x": {"type":"integer"}, "y": {"type":"integer"}, "w": {"type":"integer"}, "h": {"type":"integer"} }, "required": ["x","y","w","h"] } ] }` and add `"detectedBox"` to `required`. (If the generator mishandles `oneOf`, use `"type": ["object","null"]` with the same properties; check the generated TS and Python.)
  - `recutstatsband.schema.json`: add `"adjusted": { "type": "integer" }` and to `required`.
  - `recutadjustrequest.schema.json`: title `RecutAdjustRequest`, description "The corrected box of a recut proposal, in pixels of the source image", `additionalProperties: false`, the four integer properties (`x`,`y` `minimum: 0`; `w`,`h` `minimum: 16`), all required.
  - Repository: in `get()` add
    ```python
    "detectedBox": None if row.detected_crop_x is None else {
        "x": row.detected_crop_x, "y": row.detected_crop_y, "w": row.detected_crop_w, "h": row.detected_crop_h},
    ```
    In `band_stats()`: select `func.count().filter(ImageCut.detected_crop_x.is_not(None))` as a fourth column; accumulate it under the key `"adjusted"` of `per_band[label]` (it is a count, not a state, so keep it out of the state totals logic: sum it into `totals["adjusted"]` the same way); `_stats_band` returns `"adjusted": counts.get("adjusted", 0)`.
  - Regenerate the three type trees with the commands in `CLAUDE.md` ("Type generation") and `documents/generation.md` (Python: run from `Backend/`). Revert timestamp-only churn in untouched generated files with `git checkout -- <file>`, keeping `__init__.py` re-exports and the new/changed modules.
  - Update `Frontend/memes-frontend/src/recut/fixtures.ts` (`makeProposalDetail` gets `detectedBox: null`; `makeStats` bands and total get `adjusted: 0`) and `src/recut/types.ts` (export `RecutAdjustRequest`).
- [ ] **Step 4: Run to verify pass:** integration files, `cd Backend && pytest tests/test_recut_endpoints.py`, then `cd Frontend/memes-frontend && npx tsc -b` (fixtures compile).
- [ ] **Step 5: Commit** (`feat(recut): R6 schemas and detected box in the read side`).

---

### Task 4: The `adjusted` metric

**Files:**
- Modify: `Backend/app/services/business_metrics_service.py`
- Test: `tests/integration/test_business_metrics_service.py`, `Backend/tests/test_admin_metrics_endpoint.py`, `Frontend/memes-frontend/src/test/mockApi.ts`, `Frontend/memes-frontend/src/components/BusinessMetricsPanel.test.tsx`

**Interfaces:**
- Produces: process `recut` events `("proposed", "adjusted", "approved", "rejected")`; metric `adjusted_share` = `adjusted / proposed` (`None` when no proposals); `adjusted` NOT in any exclusive group.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_business_metrics_service.py`: (a) recording `adjusted` then `approved` on one subject is allowed and both count; (b) `adjusted` recorded twice for one subject is counted once (second `record` returns False); (c) a bucket with `proposed=4, adjusted=1` has `adjusted_share == 0.25` and with `proposed=0` it is `None`; (d) the existing count-dictionary assertions now list `adjusted: 0`. Run → FAIL (`UnknownEventError`).
- [ ] **Step 2: Implement.** `events=("proposed", "adjusted", "approved", "rejected")`; add `"adjusted_share": lambda c: _ratio(c["adjusted"], c["proposed"]),` to `metrics`; leave `exclusive` unchanged.
- [ ] **Step 3: Fix dependants.** Run the full `tests/integration/` file set that mentions `batch_metrics`/`approve_rate` (`tests/integration/test_business_metrics_service.py`, `tests/integration/test_recut_stats.py`) and `cd Backend && pytest tests/test_admin_metrics_endpoint.py`; update expected dicts. Check `shared/schemas` for the batch-metrics schema (`grep -l approve_rate shared/schemas`): if `counts`/`metrics` are fixed-key objects, add `adjusted` / `adjusted_share` (regenerate types as in Task 3); if they are free maps, change nothing. Update `src/test/mockApi.ts` (`counts` gains `adjusted: 0`, `metrics` gains `adjusted_share: null`) and the panel test if it asserts the column set; the panel renders count keys generically (`Object.keys(sample.counts)`), so a new column appears without a component change; add one assertion that an "adjusted" header is shown.
- [ ] **Step 4: Verify:** `tests/integration/` metrics files, Backend endpoint test, `vitest run src/components/BusinessMetricsPanel.test.tsx src/pages/AdminBatchesPage.test.tsx`.
- [ ] **Step 5: Commit** (`feat(recut): R6 adjusted metric`).

---

### Task 5: File effects tied to the transaction

**Files:**
- Create: `Backend/app/services/recut_files.py`
- Test: `tests/integration/test_recut_files.py`

**Interfaces:**
- Produces (all in `Backend.app.services.recut_files`):
  - `file_effects(session: AsyncSession, on_commit: Callable[[], None] | None = None, on_rollback: Callable[[], None] | None = None) -> None` — runs `on_commit` once after the session's outermost transaction commits, or `on_rollback` once after it rolls back; whichever happens first cancels the other; exceptions from the callbacks are logged and swallowed.
  - `remove_quietly(path: str) -> None` — `os.remove`, ignores `OSError`, logs others.
  - `copy_to_pre_cuts(base_path: str, filename: str) -> None` — see Task 6.

- [ ] **Step 1: Write the failing tests** (`tests/integration/test_recut_files.py`, `@pytest.mark.asyncio(loop_scope="session")`, fixture `db_session`):

```python
async def test_on_commit_runs_after_commit_and_not_before(db_session):
    calls = []
    file_effects(db_session, on_commit=lambda: calls.append("commit"), on_rollback=lambda: calls.append("rollback"))
    await db_session.flush()
    assert calls == []
    await db_session.commit()
    assert calls == ["commit"]


async def test_on_rollback_runs_after_rollback_and_the_commit_hook_is_cancelled(db_session):
    calls = []
    file_effects(db_session, on_commit=lambda: calls.append("commit"), on_rollback=lambda: calls.append("rollback"))
    await db_session.rollback()
    assert calls == ["rollback"]
    await db_session.commit()
    assert calls == ["rollback"]


async def test_a_savepoint_rollback_does_not_fire_the_rollback_hook(db_session):
    calls = []
    file_effects(db_session, on_rollback=lambda: calls.append("rollback"))
    savepoint = await db_session.begin_nested()
    await savepoint.rollback()
    assert calls == []
    await db_session.rollback()
    assert calls == ["rollback"]


async def test_a_failing_callback_is_swallowed(db_session):
    def boom():
        raise RuntimeError("disk gone")
    file_effects(db_session, on_commit=boom)
    await db_session.commit()          # must not raise


def test_remove_quietly_ignores_a_missing_file(tmp_path):
    remove_quietly(str(tmp_path / "missing.jpg"))
```
- [ ] **Step 2: Run to verify failure** (module missing).
- [ ] **Step 3: Implement.**

```python
"""File side effects of a recut request, tied to the transaction that get_async_db owns (screenshot recut R6)."""
import logging
import os
import shutil
from typing import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
PRE_CUTS_DIR = "pre-cuts"


def remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.exception("recut: could not remove %s", path)


def file_effects(session: AsyncSession, on_commit: Callable[[], None] | None = None,
                 on_rollback: Callable[[], None] | None = None) -> None:
    sync = session.sync_session
    state = {"done": False}

    def settle(action):
        if state["done"]:
            return
        state["done"] = True
        event.remove(sync, "after_commit", committed)
        event.remove(sync, "after_soft_rollback", rolled_back)
        if action is None:
            return
        try:
            action()
        except Exception:
            logger.exception("recut: a file action after the transaction failed")

    def committed(_session):
        settle(on_commit)

    def rolled_back(_session, previous_transaction):
        if previous_transaction.parent is None:        # a savepoint rolling back is not the request failing
            settle(on_rollback)

    event.listen(sync, "after_commit", committed)
    event.listen(sync, "after_soft_rollback", rolled_back)
```
(`copy_to_pre_cuts` is added in Task 6.) If `event.remove` inside the handler misbehaves on this SQLAlchemy version, replace it with the `state["done"]` flag alone and leave the listeners attached (the session is per request); record the ruling.
- [ ] **Step 4: Run to verify pass.** If the savepoint test fails because `after_soft_rollback` reports a root transaction for the test fixture's join mode, rule on the smallest change (e.g. test with a dedicated `AsyncSession(db_engine)` for the rollback cases) and ledger it.
- [ ] **Step 5: Commit** (`feat(recut): R6 transaction-bound file effects`).

---

### Task 6: Pre-cuts copy on approve

**Files:**
- Modify: `Backend/app/services/recut_files.py`, `Backend/app/services/recut_decision_service.py`, `Backend/app/api/recut.py` (no change expected: the service reads `BASE_PATH` itself)
- Test: `tests/integration/test_recut_decision_service.py`

**Interfaces:**
- Consumes: `file_effects`, `PRE_CUTS_DIR` from Task 5.
- Produces: `copy_to_pre_cuts(base_path: str, filename: str) -> None`; `RecutDecisionService(session, base_path: str | None = None)` — `base_path=None` reads `settings.get("BASE_PATH")` when approving.

- [ ] **Step 1: Write failing tests** (in `test_recut_decision_service.py`; use `tmp_path` as `base_path`; create the source file `tmp_path/<source.filename>` with bytes `b"source"`; because `db_session` never really commits, call `await db_session.commit()` after `approve` to fire the hook):
  - approve + commit → `tmp_path/"pre-cuts"/source.filename` exists with the same bytes;
  - before the commit the copy does not exist;
  - an existing `pre-cuts/<name>` with other bytes is not overwritten;
  - source file missing → approve still returns `state == "approved"` and no exception at commit;
  - `base_path` unwritable (pass a path under a regular file) → approve still succeeds;
  - reject + commit → `pre-cuts` directory does not exist;
  - approve that fails a precondition (`source_flagged`) registers nothing: no copy after commit.
  Run → FAIL (`__init__` takes no `base_path`).
- [ ] **Step 2: Implement.** In `recut_files.py`:

```python
def copy_to_pre_cuts(base_path: str, filename: str) -> None:
    """Back up the source file of an approved cut next to the library, never overwriting (R6, decision 7)."""
    name = os.path.basename(filename)
    source = os.path.join(base_path, filename)
    target = os.path.join(base_path, PRE_CUTS_DIR, name)
    try:
        if os.path.exists(target):
            return
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target)
    except OSError:
        logger.exception("recut: could not copy %s to pre-cuts", source)
```
In `RecutDecisionService.__init__(self, session, base_path: str | None = None)`: `self.base_path = base_path`. In `approve`, after all precondition checks pass and before `_finish`, add
```python
        base = self.base_path or settings.get("BASE_PATH")
        if base:
            file_effects(self.session, on_commit=lambda: copy_to_pre_cuts(base, source.filename))
```
(import `settings` from `config.settings`; `source.filename` is read eagerly into a local first: `filename = source.filename`, because the ORM row may be expired after the commit). Update the module docstring: "A decision moves no file; an approval also copies the source to pre-cuts/ after the commit."
- [ ] **Step 3: Run to verify pass**, then the whole `tests/integration/test_recut_decision_service.py` and `cd Backend && pytest tests/test_recut_endpoints.py`.
- [ ] **Step 4: Commit** (`feat(recut): R6 copy the source to pre-cuts on approve`).

---

### Task 7: `RecutAdjustService`

**Files:**
- Create: `Backend/app/services/recut_adjust_service.py`
- Modify: `Backend/app/repositories/recut_repository.py` (add `replace_cut`, `delete_image`)
- Test: `tests/integration/test_recut_adjust_service.py`

**Interfaces:**
- Consumes: `lock_proposal`, `images_by_id` (existing); `ProposalNotFoundError`, `ProposalConflictError` from `recut_decision_service`; `file_effects`, `remove_quietly` (Task 5); `crop_to_file`, `CutError` and reason constants from `batch.utils.recut_cut`; `CutCopier.copy(source_id, cut_id, crop)`; `BusinessMetricsService.record`.
- Produces:
  - `MIN_SIDE = 16`; `class InvalidBoxError(Exception)` with `.message`; `class ProposalWriteError(Exception)`.
  - `RecutAdjustService(session, base_path: str | None = None, jpeg_quality: int | None = None)`; `async adjust(cut_id: uuid.UUID, x: int, y: int, w: int, h: int) -> None`.
  - `RecutRepository.replace_cut(row: ImageCut, new_image_id: uuid.UUID, box: tuple[int,int,int,int]) -> None` (sets the box, stores `detected_crop_*` from the current box when all four are NULL, sets `cut_image_id`, flushes); `RecutRepository.delete_image(image_id) -> None` (core `delete(Image)`).

- [ ] **Step 1: Write the failing tests** (`tests/integration/test_recut_adjust_service.py`). Helpers: a function that writes a real image (`PIL.Image.new("RGB", (400, 300), "white")`, JPEG and PNG variants, saved to `tmp_path`), computes `sha256_file`, inserts the source `Image` (status active, filename = the file's name, width/height), the old cut `Image` (status `cut_proposed`, its own small file in `tmp_path`), an `ImageCut` (`state=proposed`, box `(10, 20, 300, 200)`, `source_content_hash=<hash>`, `detect_run_id` None), an `OCRText` row on the source with a bbox inside the new box, and returns `(row, source, old_cut, tmp_path)`. Service under test: `RecutAdjustService(db_session, base_path=str(tmp_path), jpeg_quality=90)`. Tests (each named for the behaviour):
  1. `test_adjust_replaces_the_cut_image_and_keeps_the_proposal`: after `adjust(row.id, 50, 40, 200, 120)` → `row.state == "proposed"`, box `(50,40,200,120)`, `row.cut_image_id != old_cut.id`; the old `Image` row is gone; a new `Image` with status `cut_proposed`, `width == 200`, `height == 120`, `created_at == source.created_at`; the new file exists in `tmp_path` and opens at size `(200, 120)` with the source's format; `detected_crop_*` equals `(10,20,300,200)`; `confidence` unchanged; the copied `OCRText` has `stale` true with its bbox shifted by `(-50,-40)`.
  2. `test_a_png_source_gives_a_png_cut` (format and size).
  3. `test_a_second_adjustment_keeps_the_first_detector_box`: adjust twice; `detected_crop_*` still `(10,20,300,200)`, box is the second one, and the first new image is gone.
  4. `test_adjusting_back_to_the_detected_box_is_allowed` (second call with the detected box succeeds; it differs from the current box).
  5. `test_the_adjusted_event_is_recorded_once`: `process_events.subject_has_any_event(db_session, "recut", row.id, ("adjusted",))` true after one call; after two calls the count of `adjusted` events for the subject is 1.
  6. Parametrized 422 cases, each asserting `InvalidBoxError` and that `tmp_path` still holds exactly the files it held before (no new file) and the row is unchanged: `w=15`, `h=15`, box equal to the current box, `x + w > 400`, `y + h > 300`.
  7. Parametrized 409 cases with `ProposalConflictError.code`: state `approved` → `already_decided`; cut image deleted from the row (`cut_image_id=None`) → `cut_missing`; source `Image` status `pending` → `source_not_active`; source flagged (`ImageExtrasRepository(db_session).set_flagged(source.id, True, "x")`) → `source_flagged`; `source_image_id=None` → `source_missing`; source file deleted from disk → `source_missing`; source file overwritten with different valid pixels → `source_changed`; source file replaced with garbage bytes but the hash column set to `None` → `source_unreadable`. Every case leaves no new file.
  8. `test_unknown_proposal_is_not_found` → `ProposalNotFoundError`.
  9. `test_the_old_file_is_removed_only_after_the_commit`: after `adjust`, the old cut's file still exists; after `await db_session.commit()` it is gone.
  10. `test_a_rollback_removes_the_new_file_and_keeps_the_old_one`: after `adjust` the new file exists; `await db_session.rollback()` → the new file is gone, the old file still exists.
  11. `test_a_failure_after_the_file_is_written_leaves_no_orphan`: monkeypatch `CutCopier.copy` to raise `RuntimeError`; `adjust` raises; `await db_session.rollback()`; no new file in `tmp_path`.
  12. `test_a_write_error_is_a_500_error`: monkeypatch `Backend.app.services.recut_adjust_service.crop_to_file` to raise `CutError(WRITE_ERROR)` → `ProposalWriteError`.
- [ ] **Step 2: Run to verify failure** (module missing).
- [ ] **Step 3: Implement.** Repository methods:

```python
    async def replace_cut(self, row: ImageCut, new_image_id: uuid.UUID, box: tuple[int, int, int, int]) -> None:
        if row.detected_crop_x is None:
            row.detected_crop_x, row.detected_crop_y = row.crop_x, row.crop_y
            row.detected_crop_w, row.detected_crop_h = row.crop_w, row.crop_h
        row.crop_x, row.crop_y, row.crop_w, row.crop_h = box
        row.cut_image_id = new_image_id
        await self.session.flush()

    async def delete_image(self, image_id: uuid.UUID) -> None:
        await self.session.execute(delete(Image).where(Image.id == image_id))
```
(add `delete` to the sqlalchemy import). Service:

```python
"""Adjust the box of one recut proposal and rebuild its cut (screenshot recut R6). One adjustment is one transaction
owned by the caller (never commits); every precondition is checked before the first write."""
import asyncio
import os
import uuid

from PIL import Image as PILImage
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Backend.app.services.recut_decision_service import ProposalConflictError, ProposalNotFoundError
from Backend.app.services.recut_files import file_effects, remove_quietly
from batch.utils.recut_cut import SOURCE_CHANGED, SOURCE_MISSING, UNREADABLE, CutError, crop_to_file
from config.settings import settings
from repository.cut_copier import CutCopier
from repository.image_extras import ImageExtrasRepository
from Storage.models import CutState, Image, ImageStatus

MIN_SIDE = 16


class InvalidBoxError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProposalWriteError(Exception):
    """The new cut file could not be written (HTTP 500, detail write_error)."""


def _source_size(path: str) -> tuple[int, int]:
    try:
        with PILImage.open(path) as im:
            return im.size
    except FileNotFoundError:
        raise ProposalConflictError("source_missing") from None
    except (OSError, ValueError, SyntaxError, PILImage.DecompressionBombError):
        raise ProposalConflictError("source_unreadable") from None


class RecutAdjustService:
    def __init__(self, session: AsyncSession, base_path: str | None = None, jpeg_quality: int | None = None):
        self.session = session
        self.repo = RecutRepository(session)
        self.extras = ImageExtrasRepository(session)
        self.base_path = base_path
        self.jpeg_quality = jpeg_quality

    async def adjust(self, cut_id: uuid.UUID, x: int, y: int, w: int, h: int) -> None:
        base = self.base_path or settings.get("BASE_PATH")
        quality = self.jpeg_quality or settings.RECUT.JPEG_QUALITY
        row = await self.repo.lock_proposal(cut_id)
        if row is None:
            raise ProposalNotFoundError(str(cut_id))
        if row.state != CutState.PROPOSED:
            raise ProposalConflictError("already_decided")
        wanted = [i for i in (row.source_image_id, row.cut_image_id) if i is not None]
        images = await self.repo.images_by_id(wanted) if wanted else {}
        old_cut = images.get(row.cut_image_id) if row.cut_image_id is not None else None
        if old_cut is None or old_cut.status != ImageStatus.CUT_PROPOSED:
            raise ProposalConflictError("cut_missing")
        source = images.get(row.source_image_id) if row.source_image_id is not None else None
        if source is None:
            raise ProposalConflictError("source_missing")
        if source.status != ImageStatus.ACTIVE:
            raise ProposalConflictError("source_not_active")
        if (await self.extras.get_flags_bulk([source.id]))[source.id]:
            raise ProposalConflictError("source_flagged")

        box = (x, y, w, h)
        if (row.crop_x, row.crop_y, row.crop_w, row.crop_h) == box:
            raise InvalidBoxError("The box is the current box")
        if x < 0 or y < 0 or w < MIN_SIDE or h < MIN_SIDE:
            raise InvalidBoxError(f"The box must start at 0 or more and be at least {MIN_SIDE} px on each side")
        src = os.path.join(base, source.filename)
        width, height = await asyncio.to_thread(_source_size, src)
        if x + w > width or y + h > height:
            raise InvalidBoxError(f"The box does not fit the source ({width}x{height})")

        dst_name = f"{uuid.uuid4()}{os.path.splitext(source.filename)[1]}"
        dst = os.path.join(base, dst_name)
        old_path = os.path.join(base, old_cut.filename)
        try:
            cut_file = await asyncio.to_thread(crop_to_file, src, dst, box, row.source_content_hash, quality)
        except CutError as error:
            if error.reason in (SOURCE_MISSING, SOURCE_CHANGED):
                raise ProposalConflictError(error.reason) from None
            if error.reason == UNREADABLE:
                raise ProposalConflictError("source_unreadable") from None
            raise ProposalWriteError(error.reason) from None
        file_effects(self.session, on_commit=lambda: remove_quietly(old_path), on_rollback=lambda: remove_quietly(dst))

        new = Image(id=uuid.uuid4(), filename=dst_name, content_hash=cut_file.content_hash, width=cut_file.width,
                    height=cut_file.height, status=ImageStatus.CUT_PROPOSED, created_at=source.created_at)
        self.session.add(new)
        await self.session.flush()
        await CutCopier(self.session).copy(source.id, new.id, box)
        old_id = old_cut.id
        await self.repo.replace_cut(row, new.id, box)
        await self.repo.delete_image(old_id)
        await BusinessMetricsService(self.session).record("recut", "adjusted", row.detect_run_id, row.id)
```
Note the `SOURCE_CHANGED` mapping: the existing hash check and the in-function box check share that reason; the pre-check above makes the latter unreachable except by a race. If the failure-after-write case (test 11) leaves `dst` because `file_effects` was registered after `crop_to_file` returned, that is correct: the hook is registered immediately after the file exists and before any SQL. Keep that order.
- [ ] **Step 4: Run to verify pass** (the new file; then `tests/integration/test_create_recut_cuts.py`, `test_recut_decision_service.py`, `test_recut_stats.py`).
- [ ] **Step 5: Commit** (`feat(recut): R6 adjust service`).

---

### Task 8: Adjust endpoint and `backend_api.md`

**Files:**
- Modify: `Backend/app/api/recut.py`, `backend_api.md`
- Test: `Backend/tests/test_recut_endpoints.py`

**Interfaces:**
- Consumes: `RecutAdjustService`, `InvalidBoxError`, `ProposalWriteError`; generated `Backend.app.types.generated.recutadjustrequest.Schema as RecutAdjustRequest`.
- Produces: `POST /api/recut/proposals/{proposal_id}/adjust` → `RecutProposalDetail`; dependency `get_recut_adjust_service`.

- [ ] **Step 1: Write failing tests** (same fixture style; override `get_recut_adjust_service` with a stub holding `adjust = AsyncMock()`, and `get_recut_repository` already returns `repo.get = AsyncMock(return_value=DETAIL)`):
  - `200`: body `{"x":50,"y":40,"w":200,"h":120}` → response equals `DETAIL`; `adjust` awaited with `(UUID(id), 50, 40, 200, 120)`; `repo.get` awaited with the id.
  - `404` when `adjust` raises `ProposalNotFoundError`; `409` with `detail == "source_changed"` for `ProposalConflictError("source_changed")`; `422` with a string `detail` for `InvalidBoxError("x")`; `500` with `detail == "write_error"` for `ProposalWriteError`.
  - `422` and `adjust` not awaited for: non-UUID id, missing field, `"w": 15`, `"x": -1`, `"x": 1.5`, `"x": "a"`.
  - `404` if `repo.get` returns `None` after a successful adjust (cannot happen normally; keep the guard).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** in `recut.py`:

```python
async def get_recut_adjust_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[RecutAdjustService, None]:
    yield RecutAdjustService(db)


@router.post("/proposals/{proposal_id}/adjust", response_model=RecutProposalDetail)
async def adjust_proposal(proposal_id: uuid.UUID, body: RecutAdjustRequest,
                          service: RecutAdjustService = Depends(get_recut_adjust_service),
                          repo: RecutRepository = Depends(get_recut_repository)):
    # Raising after the service wrote anything is safe: get_async_db rolls the whole request back (and the file hooks run).
    try:
        await service.adjust(proposal_id, body.x, body.y, body.w, body.h)
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except ProposalConflictError as error:
        raise HTTPException(status_code=409, detail=error.code) from None
    except InvalidBoxError as error:
        raise HTTPException(status_code=422, detail=error.message) from None
    except ProposalWriteError:
        raise HTTPException(status_code=500, detail="write_error") from None
    detail = await repo.get(proposal_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return detail
```
`backend_api.md` "Recut review" section: add the endpoint (`Request` body `{ x, y, w, h }` integers; responses 200/404/409 codes/422/500 as in the Global Constraints), `detectedBox` in the `RecutProposalDetail` description, `adjusted` in the `RecutStats` band description, and replace "a decision moves no file" by "a decision moves no file (removal is `move_flagged`, then `unregister_deleted_images`); an approval also copies the source file to `BASE_PATH/pre-cuts/` after the commit". Also mention `adjusted` in the metrics event list near line 1550 if it enumerates events.
- [ ] **Step 4: Run to verify pass:** `cd Backend && pytest tests/test_recut_endpoints.py`, then the whole Backend root.
- [ ] **Step 5: Commit** (`feat(recut): R6 adjust endpoint`).

---

### Task 9: Box editing helpers (frontend, pure)

**Files:**
- Create: `Frontend/memes-frontend/src/recut/boxEdit.ts`
- Test: `Frontend/memes-frontend/src/recut/boxEdit.test.ts`

**Interfaces:**
- Produces (all pure; `Box`, `Size` from `./boxOverlay`):
  - `MIN_SIDE = 16`
  - `type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"`
  - `moveBox(box, dx, dy, size): Box`
  - `resizeBox(box, handle, dx, dy, size): Box`
  - `isValidBox(box, size): boolean`
  - `sameBox(a: Box, b: Box): boolean`
  - `parseCoordinate(text: string): number | null`

- [ ] **Step 1: Write the failing tests** with `size = { width: 1000, height: 800 }`, `box = { x: 100, y: 100, w: 300, h: 200 }`:
  - `moveBox(box, 50, -30, size)` → `{x:150,y:70,w:300,h:200}`; `moveBox(box, 900, 0, size)` → `x: 700`; `moveBox(box, -500, -500, size)` → `x:0,y:0`; fractional deltas are rounded (`moveBox(box, 10.6, 0, size).x === 111`).
  - `resizeBox(box,"e",50,0,size)` → `w:350`; `"w",40,0` → `{x:140,w:260}`; `"nw",-200,-200` → `{x:0,y:0,w:400,h:300}`; `"se",5000,5000` → `{w:900,h:700}`; `"e",-290,0` → `w:16`; `"w",290,0` → `{x:384,w:16}`; `"n",0,-500` → `{y:0,h:300}`; `"s",0,-195` → `h:16`.
  - `isValidBox`: valid box true; `w:15` false; `x:-1` false; `x:800,w:300` false (overflows); `h:801` false; non-integer false.
  - `sameBox` true/false; `parseCoordinate(" 42 ")` → 42, `""`/`"-1"`/`"1.5"`/`"abc"`/`"1e3"` → `null`, `"0"` → 0.
- [ ] **Step 2: Run to verify failure** (`cd Frontend/memes-frontend && npx vitest run src/recut/boxEdit.test.ts`).
- [ ] **Step 3: Implement.**

```ts
import type { Box, Size } from "./boxOverlay"

export const MIN_SIDE = 16
export type Handle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw"

const clamp = (value: number, low: number, high: number): number => Math.max(low, Math.min(high, value))

export function moveBox(box: Box, dx: number, dy: number, size: Size): Box {
  return {
    ...box,
    x: clamp(box.x + Math.round(dx), 0, size.width - box.w),
    y: clamp(box.y + Math.round(dy), 0, size.height - box.h),
  }
}

export function resizeBox(box: Box, handle: Handle, dx: number, dy: number, size: Size): Box {
  const moveX = Math.round(dx)
  const moveY = Math.round(dy)
  let left = box.x
  let right = box.x + box.w
  let top = box.y
  let bottom = box.y + box.h
  if (handle.includes("w")) left = clamp(left + moveX, 0, right - MIN_SIDE)
  if (handle.includes("e")) right = clamp(right + moveX, left + MIN_SIDE, size.width)
  if (handle.includes("n")) top = clamp(top + moveY, 0, bottom - MIN_SIDE)
  if (handle.includes("s")) bottom = clamp(bottom + moveY, top + MIN_SIDE, size.height)
  return { x: left, y: top, w: right - left, h: bottom - top }
}

export function isValidBox(box: Box, size: Size): boolean {
  const whole = [box.x, box.y, box.w, box.h].every(Number.isInteger)
  return whole && box.x >= 0 && box.y >= 0 && box.w >= MIN_SIDE && box.h >= MIN_SIDE
    && box.x + box.w <= size.width && box.y + box.h <= size.height
}

export const sameBox = (a: Box, b: Box): boolean => a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h

export function parseCoordinate(text: string): number | null {
  const trimmed = text.trim()
  return /^\d+$/.test(trimmed) ? Number(trimmed) : null
}
```
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** (`feat(recut): R6 box editing helpers`).

---

### Task 10: API client, mock and queue hook (`adjust`)

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/recut/useRecutQueue.ts`, `src/recut/fixtures.ts`
- Test: `src/api/http/HttpMemesApi.recut.test.ts`, `src/recut/useRecutQueue.test.ts`

**Interfaces:**
- Produces:
  - `MemesApi.adjustRecutProposal(id: string, box: Box): Promise<RecutProposalDetail>` (`Box` from `recut/boxOverlay`).
  - `useRecutQueue(...)` additionally returns `adjust(box: Box): Promise<AdjustResult>` with `type AdjustResult = { outcome: "done" | "ignored" | "invalid" | "failed" | "conflict" }`.

- [ ] **Step 1: Write failing tests.**
  - `HttpMemesApi.recut.test.ts` (mirror the approve tests): `adjustRecutProposal("p1", {x:5,y:6,w:100,h:50})` POSTs to `/api/recut/proposals/p1/adjust` with `Content-Type: application/json` and body `{"x":5,"y":6,"w":100,"h":50}`, resolves with the parsed detail; a 409 `{"detail":"source_changed"}` rejects with `CuratorRequestError` `status 409`, `code "source_changed"`; a 422 rejects with `status 422`.
  - `useRecutQueue.test.ts` (reuse its harness): after the detail of `p1` is loaded, `adjust(box)` calls the API; on success `detail` becomes the returned detail (new `cutImageId`, new box) and the list item for `p1` takes the new box and `cutImageId`; the total is unchanged; outcome `done`. A 409 → outcome `conflict`, `decision` is `{status:"conflict", code, redirect:true}` and the queue reloads. A 422 → outcome `invalid`, `decision` stays idle. A network failure → `failed`, `decision` stays idle. `adjust` is `ignored` while a decision or another adjustment is in flight (`decidingRef`), and when the detail is not `proposed`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** Interface line in `MemesApi.ts` after `rejectRecutProposal`; `HttpMemesApi.adjustRecutProposal` mirrors `decideRecutProposal` (read it at ~line 694 and copy its `fetch`/error handling, with `method: "POST"`, JSON body, and `Accept` header; the success body is a `RecutProposalDetail`); `mockApi.ts`: `adjustRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail('p1', { cutImageId: 'cut-p1b' }))`. In `useRecutQueue.ts`:

```ts
export type AdjustResult = { outcome: "done" | "ignored" | "invalid" | "failed" | "conflict" }

  const adjust = useCallback(async (box: Box): Promise<AdjustResult> => {
    const current = loaded !== null && loaded.id === openId && loaded.state.status === "ready" ? loaded.state.data : null
    if (current === null || current.state !== "proposed" || decidingRef.current) return { outcome: "ignored" }
    decidingRef.current = true
    try {
      const updated = await memesApi.adjustRecutProposal(current.id, box)
      setLoaded({ id: current.id, state: { status: "ready", data: updated } })
      setList((previous) => ({
        ...previous,
        items: previous.items.map((item) => item.id === updated.id
          ? { ...item, box: updated.box, cutImageId: updated.cutImageId } : item),
      }))
      return { outcome: "done" }
    } catch (error) {
      if (error instanceof CuratorRequestError && error.status === 409) {
        setDecision({ status: "conflict", code: error.code ?? "", redirect: true })
        reload()
        return { outcome: "conflict" }
      }
      if (error instanceof CuratorRequestError && error.status === 422) return { outcome: "invalid" }
      return { outcome: "failed" }
    } finally {
      decidingRef.current = false
    }
  }, [memesApi, loaded, openId, reload])
```
and add `adjust` to the returned object; import `Box` type. The page also needs to know a request is in flight: add `const [adjusting, setAdjusting] = useState(false)` set true/false around the call and return `adjusting` from the hook (set it in the same try/finally).
- [ ] **Step 4: Run to verify pass** (`npx vitest run src/api src/recut`), `npx tsc -b`.
- [ ] **Step 5: Commit** (`feat(recut): R6 adjust client and queue hook`).

---

### Task 11: `BoxEditor` component and `BeforeAfter` edit mode

**Files:**
- Create: `Frontend/memes-frontend/src/components/recut/BoxEditor.tsx`, `src/components/recut/BoxEditor.test.tsx`
- Modify: `src/components/recut/BeforeAfter.tsx`
- Test: `src/components/recut/BeforeAfter.test.tsx` (create if absent)

**Interfaces:**
- Consumes: `moveBox`, `resizeBox`, `Handle` (Task 9); `overlayStyle`.
- Produces: `<BoxEditor box={Box} size={Size} onChange={(box: Box) => void} />` (rendered inside the `relative` wrapper of the source image); `BeforeAfter` gets optional props `draft?: Box | null` and `onDraftChange?: (box: Box) => void`: when `draft` is not null the source pane shows the editor for the draft instead of the static overlay and the "Removes N%" line uses the draft.

- [ ] **Step 1: Write failing tests** (`BoxEditor.test.tsx`; jsdom has no layout, so give the container a rect: `vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 500, height: 400, right: 500, bottom: 400, x: 0, y: 0, toJSON() {} })`; with `size = {width: 1000, height: 800}` one CSS pixel is two image pixels):
  - renders a `role="group"` named "Crop box editor" with eight elements `data-handle` in `n s e w ne nw se sw` plus the body `data-testid="recut-box-body"`;
  - pointer down on the body at (100,100), move to (125,90), up → `onChange` last called with the box moved by `(+50,-20)` image px;
  - pointer down on the `se` handle, move by `(+10,+5)` CSS px → `w`/`h` grow by `20`/`10`;
  - a move after pointer up does nothing;
  - moves are ignored when no drag started.
  In `BeforeAfter.test.tsx`: without `draft` the static `recut-box` overlay shows; with `draft` the editor group shows, the static overlay does not, and the "Removes N%" text reflects the draft (`size` comes from firing `load` on the source `<img>` after defining `naturalWidth`/`naturalHeight`).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** `BoxEditor`: an absolutely positioned container `div` (`className="absolute inset-0"`, `role="group"`, `aria-label="Crop box editor"`, ref used only in handlers) holding the box div (`data-testid="recut-box-body"`, `style={overlayStyle(box, size)}`, `className="absolute cursor-move touch-none border-2 border-red-500 bg-red-500/10"`) with eight handle `div`s (`data-handle`, 12 px squares positioned at corners/edge midpoints, `touch-none`, cursors `nwse-resize` etc.). Interaction state lives in a `useRef<{ kind: "move" | Handle; startX: number; startY: number; start: Box } | null>`; `onPointerDown` (on body and each handle, `event.stopPropagation()`) stores the start and calls `event.currentTarget.setPointerCapture?.(event.pointerId)`; `onPointerMove` on the container converts `(clientX - startX)` to image px with `size.width / rect.width` (and height) and calls `onChange(moveBox(...)` or `resizeBox(...))` from the start box (always from the start box, not the last box, so rounding does not accumulate); `onPointerUp`/`onPointerCancel` clear the ref. Never read the ref during render. In `BeforeAfter`: replace `{sized && <BoxOverlay box={box} size={size} />}` by `{sized && (draft ? <BoxEditor box={draft} size={size} onChange={onDraftChange ?? (() => {})} /> : <BoxOverlay box={box} size={size} />)}` and compute the removed-share from `draft ?? box`. Change the page's `key` for `BeforeAfter` in Task 12 to include `cutImageId`.
- [ ] **Step 4: Run to verify pass:** `npx vitest run src/components/recut`, `npx tsc -b`, `npx eslint src/components/recut --max-warnings 0`.
- [ ] **Step 5: Commit** (`feat(recut): R6 box editor`).

---

### Task 12: Page edit mode, info block and stats column

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/RecutReviewPage.tsx`, `src/components/recut/ProposalInfo.tsx`, `src/components/recut/ReviewStats.tsx`
- Create: `src/components/recut/AdjustPanel.tsx` (numeric fields + Apply / Cancel / Reset)
- Test: `src/pages/RecutReviewPage.test.tsx`, `src/components/recut/ReviewStats.test.tsx`, `src/components/recut/ProposalInfo.test.tsx` (create if absent), `src/components/recut/AdjustPanel.test.tsx`

**Interfaces:**
- Consumes: `useRecutQueue().adjust/adjusting` (Task 10), `BeforeAfter` props (Task 11), `isValidBox`, `sameBox`, `parseCoordinate`.
- Produces: `<AdjustPanel draft size current detectedBox disabled onChange onApply onCancel onReset />` where `draft`/`current`/`detectedBox`: `Box`/`Box`/`Box | null`.

- [ ] **Step 1: Write failing tests.**
  - `AdjustPanel.test.tsx`: four labelled number inputs ("x", "y", "w", "h") show the draft; typing "250" in `w` calls `onChange` with `w: 250`; a non-numeric entry is not forwarded; Apply is disabled when the draft equals `current`, is invalid for `size`, or `disabled`; Apply enabled for a valid different box and calls `onApply`; Cancel calls `onCancel`; "Reset to detected box" appears only when `detectedBox` is non-null and differs from `current`, and calls `onReset`; a line "Removes N% of the source" shows.
  - Page tests (extend the existing mock-API harness; the detail of `p1` is `proposed` with `box {10,20,300,200}`; fire `load` on the source `<img>` with natural size 1000x800 first):
    1. "Adjust" button switches to edit mode: the editor group and the panel appear; Approve and Reject are disabled.
    2. In edit mode, keys `a`, `r`, `ArrowRight` do nothing (spies on `approveRecutProposal`, `rejectRecutProposal`, and the URL does not change), and typing `a` inside a numeric field does nothing.
    3. Changing `w` then Apply calls `adjustRecutProposal("p1", {x:10,y:20,w:<new>,h:200})`; on success edit mode closes, the cut `<img>` `src` uses the new `cutImageId`, and the note "Adjusted. Derived data (OCR, descriptions, embeddings) is stale until recut_pipeline has run; run it before approving." shows; Approve is enabled again.
    4. `Esc` and Cancel leave edit mode without a call.
    5. Reset: with a detail whose `detectedBox` is `{x:0,y:0,w:900,h:700}` and current box different, the button loads that box into the fields; Apply sends it.
    6. 409 `source_changed` → alert "The source file changed after detection, so the box can't be applied" and the queue reloads; 422 → alert "That box does not fit this image" and edit mode stays; other failure → alert "Could not adjust the box" and edit mode stays.
    7. While Apply is in flight the buttons are disabled (use a never-resolving promise).
  - `ProposalInfo`: when `detectedBox` is non-null shows "Adjusted" and the detector box `0, 0, 900 x 700`, plus the sentence "Confidence refers to the detector's original box"; nothing extra when null.
  - `ReviewStats`: an "Adjusted" column header and the per-band / total `adjusted` values.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
  - Page state with no effects: `const [draftState, setDraftState] = useState<{ id: string; box: Box } | null>(null)`; `const draft = draftState !== null && draftState.id === openId ? draftState.box : null`; `editing = draft !== null`. `const [noticeState, setNoticeState] = useState<{ id: string; text: string } | null>(null)` keyed the same way for the post-adjust note and `const [adjustError, setAdjustError] = useState<{ id: string; text: string } | null>(null)`.
  - "Adjust" button (in the action row, `disabled={!decidable || editing}`) sets `draftState = { id: proposal.id, box: proposal.box }` and clears the error/notice. Entering edit mode: `decidable` for Approve/Reject becomes `decidable && !editing`; the key handler returns early when `editing` for arrows and `a`/`r` (add `editing` to its deps).
  - `Esc` closes edit mode: handle it in the same `keydown` effect before the INPUT check (`if (editing && event.key === "Escape") setDraftState(null)`).
  - Apply: `const result = await adjust(draft)`; `done` → `setDraftState(null)`, set the notice text; `invalid` → error "That box does not fit this image"; `failed` → "Could not adjust the box"; `conflict` → nothing (the existing conflict alert and reload handle it) and `setDraftState(null)`.
  - Extend `CONFLICT_TEXT` with: `cut_missing`: "The cut is missing", `source_missing`: "The source image is missing", `source_not_active`: "The source image is no longer active", `source_changed`: "The source file changed after detection, so the box can't be applied", `source_unreadable`: "The source file can't be read".
  - `BeforeAfter` key becomes `` `${proposal.id}:${proposal.cutImageId}` `` and receives `draft`/`onDraftChange={(box) => setDraftState({ id: proposal.id, box })}`.
  - `AdjustPanel` is a controlled form: inputs `type="number"` `inputMode="numeric"`, `onChange` uses `parseCoordinate`; buttons `min-h-11` like the page's others; `disabled` prop disables all while saving.
  - `ProposalInfo`: add an "Adjusted" field (`detector box x, y, w x h`) and the confidence note when `detail.detectedBox !== null`.
  - `ReviewStats`: add the "Adjusted" column between "Rejected" and "Approve rate"; `Row` prints `band.adjusted`.
- [ ] **Step 4: Run to verify pass:** `npx vitest run` (all), `npx tsc -b`, `npx eslint src/ --max-warnings 0`. React Compiler rules: no setState inside effects, refs only in handlers/effects, every effect must survive a StrictMode double mount.
- [ ] **Step 5: Commit** (`feat(recut): R6 adjust mode on the review page`).

---

### Task 13: Docs, status and full verification

**Files:**
- Modify: `docs/batches/use-cases/screenshot-recut.md`, `docs/image-derived-data.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r5a-approve-reject-lifecycle-design.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-30-r6-box-adjustment-design.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`, `docs/batches/inventory.md` (only if it describes the review page decisions)
- Test: `tests/docs/`

- [ ] **Step 1: Find every statement to amend.** `grep -rn "moves no file\|no file is moved" docs backend_api.md CLAUDE.md Backend` and fix each to "a decision moves no file; an approval also copies the source to `pre-cuts/` after the commit" (R5a spec: add an "Amended by R6" note under its status block instead of rewriting its decisions).
- [ ] **Step 2: Write the docs.**
  - Runbook (`screenshot-recut.md`): a "Adjusting a box" section (Adjust button, Apply, Reset to detected box, run `recut_pipeline` before approving because an adjusted cut is a fresh copy with stale derived data); a "pre-cuts" section (every approval copies the source to `BASE_PATH/pre-cuts/`, never overwrites, nothing purges it, no job reads it); "The backend writes into `BASE_PATH`: its mount must be writable"; first-rollout step: "Real-browser check of the drag handles on a tall source image and with touch (jsdom cannot test layout)"; migration list gains `e9c4a7b3d812` (R6) after `d7a3b9e2c451` and the other revisions between them and head as `alembic` shows.
  - `docs/image-derived-data.md`: under the recut/cut copy rules, one sentence: an adjusted cut is a new image copied from the source exactly like the first (the old cut image and its rows are deleted).
  - `CLAUDE.md`: after the `recut_pipeline` entry, one short paragraph: the backend image carries Pillow and `batch/utils/{__init__,recut_cut,file_hash}.py` for the review page's box adjustment (R6), and writes cut files and `pre-cuts/` into `BASE_PATH`.
  - R6 spec: `Status: done`, add `Plan:` link; tracker row `done` with the plan link; change the epic README only if it lists R6 as pending (`grep -n "R6" docs/superpowers/specs/2026-10-17-screenshot-recut/README.md`).
- [ ] **Step 3: Full verification, each root separately** (redirect long output to a file in the scratchpad and read its tail):
  - `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/ -q`
  - `cd Backend && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest -q` (expect only the known unrelated failure)
  - `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest batch/tests/ -q`; `... tests/evaluation/ -q`; `... tests/rules/ -q`; `... tests/docs/ -q`; `... tests/docker/ -q` (skip is acceptable if no daemon)
  - `cd Frontend/memes-frontend && npx vitest run && npx tsc -b && npx eslint src/ --max-warnings 0`
  - Type regeneration diff check: rerun the three generators and confirm `git status` shows no change beyond timestamp churn (revert that churn).
  - Random failures from the shared test DB: rerun, do not "fix".
- [ ] **Step 4: Commit** (`docs(recut): R6 done`).
