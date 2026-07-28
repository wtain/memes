# Batch Run Trigger Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `trigger` column (manual/scheduled/unknown) to `batch_runs`, plus a DB-level partial unique index that atomically prevents two concurrent active runs of the same `kind`, so later specs (the universal wrapper, the admin controller) can build correct trigger attribution and a real concurrency guard on top.

**Architecture:** One Alembic migration (locked for its full duration) plus the matching SQLAlchemy model change; `BatchRunRepository.create_run()` gains a required `trigger` parameter and translates the DB's unique-violation into a new `BatchAlreadyRunningError`.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, Alembic, PostgreSQL (partial unique index), pytest + `pytest-asyncio` against the real `ocrdb_test` database (`tests/integration/`).

**Spec:** `docs/superpowers/specs/2026-07-28-batch-run-trigger-tracking-design.md`

## Global Constraints

- The migration must take an `ACCESS EXCLUSIVE` table lock on `batch_runs` as the very first
  statement in `upgrade()`, before any data cleanup/backfill — closing the window for a concurrent
  `create_run()` to race the migration.
- `trigger` is a required parameter on `create_run()` — no default value. Every caller must state
  its intent explicitly.
- The partial unique index (`UNIQUE (kind) WHERE status = 'started'`) must exist both via the Alembic
  migration (real deployments) **and** declared on the `BatchRun` model's `__table_args__` (so
  `tests/integration/`'s `Base.metadata.create_all`-based schema setup also enforces it) — the two
  must produce the identical constraint, not just similar ones.
- No behavior change to any existing call site beyond adding the now-required `trigger` argument.
- `trends_batch.py`'s call site gets `trigger="unknown"` — a deliberate, temporary placeholder (the
  next spec in the sequence replaces this call site entirely); `ingest_hash_dedup.py`'s call site
  gets `trigger="manual"` (accurate today, not temporary).

---

### Task 1: Migration + model change

**Files:**
- Create: `Storage/alembic/versions/<generated>_add_trigger_to_batch_runs.py`
- Modify: `Storage/models.py:442-484` (the `RunStatus`/`BatchRun` block)
- Test: `tests/integration/test_batch_run_schema.py` (new file)

**Interfaces:**
- Produces: `Storage.models.TriggerType` enum (`manual`/`scheduled`/`unknown`, `__str__` returns the
  value, mirroring `RunStatus`); `BatchRun.trigger: Mapped[str]` (non-nullable); a partial unique
  index named `ix_batch_runs_one_active_per_kind` on `(kind)` where `status = 'started'`. Task 2
  depends on this index existing (that's what makes `create_run`'s unique-violation catch possible)
  and on `trigger` being a real column it can set.

Current model (`Storage/models.py:442-484`, for reference — do not copy verbatim, this is what
you're diffing against):

```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

    def __str__(self) -> str:
        return self.value


class BatchRun(Base):
    __tablename__ = "batch_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=str(RunStatus.started),
    )
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="run"
    )

    def __repr__(self) -> str:
        return f"<BatchRun run_id={self.run_id} kind={self.kind!r} created_at={self.created_at}>"
```

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_batch_run_schema.py`:

```python
"""
Integration tests for the batch_runs schema itself (trigger column, one-active-per-kind
partial unique index) -- independent of BatchRunRepository, which Task 2 covers.
Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from Storage.models import BatchRun, RunStatus


@pytest.mark.asyncio(loop_scope="session")
async def test_trigger_column_round_trips(db_session):
    run = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(run)
    await db_session.flush()

    await db_session.refresh(run)
    assert run.trigger == "manual"


@pytest.mark.asyncio(loop_scope="session")
async def test_second_started_run_of_same_kind_violates_unique_index(db_session):
    first = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(first)
    await db_session.flush()

    second = BatchRun(kind="trends", trigger="scheduled", status=str(RunStatus.started))
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_second_started_run_of_different_kind_is_fine(db_session):
    first = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(first)
    await db_session.flush()

    second = BatchRun(kind="move_flagged", trigger="manual", status=str(RunStatus.started))
    db_session.add(second)
    await db_session.flush()  # must not raise


@pytest.mark.asyncio(loop_scope="session")
async def test_second_run_of_same_kind_is_fine_once_first_is_completed(db_session):
    first = BatchRun(kind="trends", trigger="manual", status=str(RunStatus.started))
    db_session.add(first)
    await db_session.flush()
    first.status = str(RunStatus.completed)
    await db_session.flush()

    second = BatchRun(kind="trends", trigger="scheduled", status=str(RunStatus.started))
    db_session.add(second)
    await db_session.flush()  # must not raise -- first is no longer 'started'
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_batch_run_schema.py -v`
Expected: FAIL — `BatchRun(...)` raises `TypeError: 'trigger' is an invalid keyword argument for BatchRun` (the column doesn't exist on the model yet).

- [ ] **Step 3: Update the model**

In `Storage/models.py`, replace the `RunStatus`/`BatchRun` block shown above with:

```python
from sqlalchemy import Index


class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

    def __str__(self) -> str:
        return self.value


class TriggerType(enum.Enum):
    manual = "manual"
    scheduled = "scheduled"
    unknown = "unknown"

    def __str__(self) -> str:
        return self.value


class BatchRun(Base):
    __tablename__ = "batch_runs"
    __table_args__ = (
        Index(
            "ix_batch_runs_one_active_per_kind", "kind",
            unique=True,
            postgresql_where=sa.text("status = 'started'"),
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=str(RunStatus.started),
    )
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="run"
    )

    def __repr__(self) -> str:
        return f"<BatchRun run_id={self.run_id} kind={self.kind!r} created_at={self.created_at}>"
```

Check the top of `Storage/models.py` for an existing `import sqlalchemy as sa` — add it if not
already present (needed for `sa.text(...)` in the index's `postgresql_where`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_batch_run_schema.py -v`
Expected: all 4 PASS. (This exercises the model via `tests/integration/conftest.py`'s
`Base.metadata.create_all` — no Alembic migration run yet, confirming the model-level index
declaration alone is sufficient for the test DB's schema.)

- [ ] **Step 5: Write the Alembic migration**

From `Storage/`, generate the revision skeleton (this produces a real, fresh revision id — do not
invent one):

```powershell
cd Storage
..\.venv311\Scripts\alembic.exe revision -m "add trigger to batch_runs"
```

This creates `Storage/alembic/versions/<generated_id>_add_trigger_to_batch_runs.py` with
`down_revision = 'fa057003a158'` (the current head) already filled in. Replace its `upgrade()`/
`downgrade()` bodies with:

```python
def upgrade() -> None:
    # First statement, before any data touches the table: blocks any concurrent create_run()
    # (scheduler tick, manual trigger) for this whole migration's transaction, closing the
    # window for a race between the cleanup UPDATE below and the index creation.
    op.execute("LOCK TABLE batch_runs IN ACCESS EXCLUSIVE MODE")

    op.add_column('batch_runs', sa.Column('trigger', sa.String(20), nullable=True))

    # Defensive cleanup: if this migration ever runs against data with more than one 'started'
    # row for the same kind (shouldn't happen given existing orphan-recovery elsewhere, but the
    # unique index below would fail outright on dirty data), keep only the most recent per kind
    # and fail the rest.
    op.execute("""
        UPDATE batch_runs SET status = 'failed', completed_at = now(),
               error = 'superseded (migration cleanup)'
        WHERE status = 'started' AND run_id NOT IN (
            SELECT DISTINCT ON (kind) run_id FROM batch_runs
            WHERE status = 'started' ORDER BY kind, created_at DESC
        )
    """)

    op.execute("UPDATE batch_runs SET trigger = 'unknown' WHERE trigger IS NULL")
    op.alter_column('batch_runs', 'trigger', nullable=False)

    op.create_index(
        'ix_batch_runs_one_active_per_kind', 'batch_runs', ['kind'],
        unique=True, postgresql_where=sa.text("status = 'started'"),
    )


def downgrade() -> None:
    op.drop_index('ix_batch_runs_one_active_per_kind', table_name='batch_runs')
    op.drop_column('batch_runs', 'trigger')
```

- [ ] **Step 6: Manually verify the migration runs cleanly against the real dev DB**

This is a schema migration — not exercised by the pytest suite (which uses `create_all`, not
Alembic). Verify it by hand against one real environment's dev database (pick one, e.g. metal):

```powershell
cd Storage
Get-Content ..\environments\.env.metal | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
..\.venv311\Scripts\alembic.exe upgrade head
..\.venv311\Scripts\alembic.exe downgrade -1
..\.venv311\Scripts\alembic.exe upgrade head
```

Expected: all three commands succeed with no errors. Leave the DB at `head` afterward (the final
`upgrade head` above does this). If this fails, do **not** proceed to Step 7 — investigate first
(a real dev DB with existing `batch_runs` rows is exactly the case the cleanup-UPDATE step exists
for; a failure here means the migration's SQL needs fixing, not that the DB is broken).

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/*_add_trigger_to_batch_runs.py tests/integration/test_batch_run_schema.py
git commit -m "feat: add trigger column and one-active-per-kind index to batch_runs"
```

---

### Task 2: Repository signature change + existing call-site updates

**Files:**
- Modify: `repository/batch_runs.py`
- Modify: `batch/ingest_hash_dedup.py:156`
- Modify: `batch/trends_batch.py:45`
- Test: `tests/integration/test_batch_runs_repository.py`

**Interfaces:**
- Consumes: `BatchRun.trigger` (Task 1), the `ix_batch_runs_one_active_per_kind` partial unique
  index (Task 1) — this task's `BatchAlreadyRunningError` only fires because that index exists.
- Produces: `BatchRunRepository.create_run(self, kind: str, trigger: str, stage: str | None = None) -> uuid.UUID`
  (breaking signature change — `trigger` is a new required positional/keyword parameter inserted
  before the existing optional `stage`); `repository.batch_runs.BatchAlreadyRunningError` (a plain
  `Exception` subclass). Both are depended on by the next spec in the sequence (the universal
  wrapper) and the one after that (the admin controller).

Current `create_run` (`repository/batch_runs.py`, for reference):

```python
async def create_run(self, kind: str, stage: str | None = None) -> uuid.UUID:
    run = BatchRun(kind=kind, status=str(RunStatus.started), stage=stage)
    self._session.add(run)
    await self._session.flush()  # populates run_id without closing the transaction
    return run.run_id
```

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_batch_runs_repository.py`, add `trigger="manual"` (or a variant, per
below) to **every** existing `create_run(...)` call in the file — the signature change below makes
`trigger` required, so every pre-existing test call needs updating regardless of what it's testing.
Also add these new tests:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_stores_trigger(db_session):
    repo = BatchRunRepository(db_session)
    run_id = await repo.create_run(kind="trends", trigger="scheduled")

    run = await repo.get_run(run_id)
    assert run.trigger == "scheduled"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_raises_when_kind_already_active(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")

    with pytest.raises(BatchAlreadyRunningError):
        await repo.create_run(kind="trends", trigger="scheduled")


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_succeeds_for_different_kind_while_one_active(db_session):
    repo = BatchRunRepository(db_session)
    await repo.create_run(kind="trends", trigger="manual")

    # must not raise
    await repo.create_run(kind="move_flagged", trigger="manual")


@pytest.mark.asyncio(loop_scope="session")
async def test_create_run_succeeds_once_prior_run_of_same_kind_is_completed(db_session):
    repo = BatchRunRepository(db_session)
    first_id = await repo.create_run(kind="trends", trigger="manual")
    await repo.commit(first_id)

    # must not raise -- first run is no longer 'started'
    await repo.create_run(kind="trends", trigger="scheduled")
```

Add `from repository.batch_runs import BatchAlreadyRunningError` to the file's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_batch_runs_repository.py -v`
Expected: FAIL — `TypeError: create_run() got an unexpected keyword argument 'trigger'` on every
call site in the file, plus `ImportError` for `BatchAlreadyRunningError` (doesn't exist yet).

- [ ] **Step 3: Implement the repository change**

In `repository/batch_runs.py`, add near the top (after the existing imports):

```python
from sqlalchemy.exc import IntegrityError


class BatchAlreadyRunningError(Exception):
    """Raised by create_run() when the one-active-per-kind partial unique index rejects a
    concurrent duplicate -- there is already a 'started' BatchRun row for this kind."""
```

Replace `create_run`:

```python
async def create_run(self, kind: str, trigger: str, stage: str | None = None) -> uuid.UUID:
    run = BatchRun(kind=kind, trigger=trigger, status=str(RunStatus.started), stage=stage)
    self._session.add(run)
    try:
        await self._session.flush()
    except IntegrityError as e:
        raise BatchAlreadyRunningError(kind) from e
    return run.run_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_batch_runs_repository.py -v`
Expected: all PASS.

- [ ] **Step 5: Update the two existing call sites**

`batch/ingest_hash_dedup.py:156` — change:
```python
batch_id = await runs_repo.create_run(kind="ingestion", stage="hash_dedup")
```
to:
```python
batch_id = await runs_repo.create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
```

`batch/trends_batch.py:45` — change:
```python
run_id = await runs_repo.create_run(kind="trends")
```
to:
```python
run_id = await runs_repo.create_run(kind="trends", trigger="unknown")
```
(Temporary and deliberate — see this file's docstring note below. The next spec in this sequence
replaces this call site entirely with the universal run wrapper's tracking, at which point this
placeholder goes away.)

Add a one-line comment directly above the `trends_batch.py` call site:
```python
# trigger="unknown" is temporary: this code path currently serves both a human running this
# script directly and the scheduler, with no way to distinguish them yet -- superseded by
# docs/superpowers/specs/2026-07-28-batch-run-wrapper-design.md.
```

- [ ] **Step 6: Run the full integration test root**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -v`
Expected: all PASS, including `test_ingest_hash_dedup.py` and `test_batch_run_schema.py` (Task 1)
alongside this task's changes — the full root, not just this file, since `repository/batch_runs.py`
is shared code per the "Running the right test scope" convention in `CLAUDE.md`.

- [ ] **Step 7: Commit**

```bash
git add repository/batch_runs.py batch/ingest_hash_dedup.py batch/trends_batch.py tests/integration/test_batch_runs_repository.py
git commit -m "feat: require trigger on create_run, add BatchAlreadyRunningError"
```

## Self-Review Notes

- **Spec coverage:** migration + table lock (Task 1), model-level index parity with the migration
  (Task 1, `__table_args__`), `create_run` signature + `BatchAlreadyRunningError` (Task 2), both
  existing call sites updated with the exact `trigger` values the spec calls for (Task 2) — all
  present.
- **Gap the spec didn't spell out, addressed here:** the spec described the Alembic migration but
  not that `tests/integration/` builds its schema via `Base.metadata.create_all` (not real Alembic
  migrations) — meaning the partial unique index needed an equivalent declaration directly on the
  `BatchRun` model (`__table_args__`) or the test suite would never actually enforce it. Task 1
  Step 3 makes this explicit and Step 4's test proves it works before the migration file is even
  written.
- **Type consistency:** `create_run(kind, trigger, stage=None)` signature and
  `BatchAlreadyRunningError` name match exactly what Task 2 test file imports and calls, and match
  what the spec documents for the next spec in the sequence to depend on.
