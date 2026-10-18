# M1: Business Metrics Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an append-only `process_events` table, `BusinessMetricsService` (record, counts and rates per batch), one read endpoint and a "Recut outcomes" panel on `/admin/batches`.

**Architecture:** One generic event table keyed `(process, event, subject_id)` with a nullable `batch_run_id`. A code registry (`PROCESSES`) holds allowed events and the rate formulas per process; rates are computed at read time. The repository (`repository/process_events.py`) never commits; the service (`Backend/app/services/business_metrics_service.py`) is importable by batch code and the API alike. The admin router exposes `GET /api/admin/batches/metrics`; a `BusinessMetricsPanel` component renders it.

**Tech Stack:** SQLAlchemy async, Alembic, PostgreSQL, FastAPI + Pydantic, React + Vitest, shared JSON schemas with generated TS/Kotlin/Python types.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-18-m1-business-metrics-design.md` (ADR: `adr/ADR-002-business-metrics.md` in the same folder).

## Global Constraints

- Branch `feat/recut-m1-business-metrics` (already created, spec committed). Commit footer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Alembic: `down_revision = 'b8d4f1a6c935'` (single head). Never apply the migration to metal, general or IT; the live rollout needs the user's go-ahead and a confirmed backup.
- Repositories never call `session.commit()`; `record` joins the caller's session.
- Never bind the metal/general/IT ports (see `environments/Environments.md`). Integration tests use `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`.
- Test roots run separately: `tests/integration/`, `Backend/` (run from `Backend/`), `tests/docs/`, `batch/tests/`, `tests/rules/`. Use `.venv311/Scripts/python -m pytest`.
- Rates are never stored (NFR-5). Zero denominator gives `None` (shown as `-`), never 0 and never an error.
- No raw SQL in routers. `backend_api.md` and the shared schema and generated types change in the same task as the router (CLAUDE.md).
- Frontend before commit: `tsc -b`, `eslint src/` with 0 warnings, `vitest run` (from `Frontend/memes-frontend/`). Effects must survive a StrictMode double mount; follow the `await Promise.resolve()` pattern used in `AdminBatchesPage.tsx`.
- Response keys on the admin router are snake_case (matches `RunStatusResponse`).

## Amendment during execution (Task 3)

`unattributed` is always present in `batch_metrics` and in the endpoint response, with all counts zero when there are none (not `null`). Reason: `AndroidClient/scripts/generate_dtos.py` cannot express a nullable object and generated a wrong `String` type; an always-present bucket keeps every generated client type correct. Where Tasks 2 to 4 below say `None`/`null` for `unattributed`, read "a bucket with zero counts"; the panel shows the Unattributed row only when some count is above zero. The code and tests in the repository are authoritative where this plan's snippets differ.

## Review Focus

Spec-implied conditions with no obvious owner; each has a pinned test in the task named.

1. A retried transaction records the same event twice: counted once (Task 2, `test_record_is_idempotent_per_subject_and_event`).
2. A `batch_runs` row is pruned after events were recorded: events survive as `unattributed` (Task 2, `test_deleting_a_run_keeps_its_events_as_unattributed`).
3. A run with proposals but no decisions yet: rates are `None`/`0.0` and pending is reported, never a division error (Task 2, `test_counts_and_rates_per_run`; Task 4, `test_shows_a_dash_for_a_missing_rate`).
4. More runs with events than `limit`: the newest runs win, newest first, and `unattributed` still comes back (Task 2, `test_limit_keeps_the_newest_runs_and_still_reports_unattributed`).
5. The metrics fetch fails or the endpoint is out of range: the page still lets the owner trigger batches, and `limit` outside 1 to 200 is a 422 (Task 3 `test_limit_out_of_range_is_422`; Task 4 `test_a_failing_metrics_fetch_leaves_the_page_usable`).

---

### Task 1: `process_events` model and migration

**Files:**
- Modify: `Storage/models.py` (add `ProcessEvent` directly after `ImageCut`, before the next class)
- Create: `Storage/alembic/versions/c5e2a9d7b140_add_process_events.py`
- Test: `tests/integration/test_process_events_migration.py`

**Interfaces:**
- Consumes: `BatchRun.run_id` (table `batch_runs`, already in `Storage/models.py`).
- Produces: `Storage.models.ProcessEvent` with columns `id, process, event, batch_run_id, subject_id, occurred_at`; unique constraint `uq_process_events_subject_event` on `(process, event, subject_id)`; index `ix_process_events_process_run_event` on `(process, batch_run_id, event)`.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_process_events_migration.py`:

```python
"""The process_events migration creates exactly what the ORM declares, and downgrades cleanly."""
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text

MIGRATION = (Path(__file__).resolve().parents[2] / "Storage" / "alembic" / "versions"
             / "c5e2a9d7b140_add_process_events.py")


def _load():
    spec = importlib.util.spec_from_file_location("migration_c5e2a9d7b140", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(connection, function):
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        function()


def _norm(value):
    """Lower-case and drop whitespace/parentheses so cosmetic renderings of the same SQL compare equal."""
    if value is None:
        return None
    return "".join(ch for ch in str(value).lower() if not ch.isspace() and ch not in "()")


def _shape(connection):
    inspector = inspect(connection)
    columns = {c["name"]: (str(c["type"]), c["nullable"], _norm(c["default"]))
               for c in inspector.get_columns("process_events")}
    uniques = {u["name"]: tuple(u["column_names"]) for u in inspector.get_unique_constraints("process_events")}
    indexes = {i["name"]: (tuple(i["column_names"]), bool(i["unique"]))
               for i in inspector.get_indexes("process_events") if not i.get("duplicates_constraint")}
    primary = tuple(inspector.get_pk_constraint("process_events")["constrained_columns"])
    foreign = {tuple(f["constrained_columns"]): (f["referred_table"], f["options"].get("ondelete"))
               for f in inspector.get_foreign_keys("process_events")}
    return columns, uniques, indexes, primary, foreign


@pytest.mark.asyncio(loop_scope="session")
async def test_the_migration_matches_the_model_and_downgrades(db_engine):
    module = _load()
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            model_shape = await connection.run_sync(_shape)
            assert model_shape[1] == {"uq_process_events_subject_event": ("process", "event", "subject_id")}
            assert model_shape[4] == {("batch_run_id",): ("batch_runs", "SET NULL")}

            await connection.execute(text("DROP TABLE process_events"))
            await connection.run_sync(lambda sync: _run(sync, module.upgrade))
            assert await connection.run_sync(_shape) == model_shape

            await connection.run_sync(lambda sync: _run(sync, module.downgrade))
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert "process_events" not in tables
        finally:
            await transaction.rollback()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("drift", [
    "ALTER TABLE process_events DROP CONSTRAINT uq_process_events_subject_event",
    "DROP INDEX ix_process_events_process_run_event",
    "ALTER TABLE process_events ALTER COLUMN occurred_at DROP DEFAULT",
])
async def test_the_shape_guard_catches_drift(db_engine, drift):
    """Negative control: a deliberately altered schema must NOT compare equal to the model-created one."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            before = await connection.run_sync(_shape)
            await connection.execute(text(drift))
            assert await connection.run_sync(_shape) != before
        finally:
            await transaction.rollback()


def test_the_migration_continues_the_chain():
    module = _load()
    assert module.revision == "c5e2a9d7b140" and module.down_revision == "b8d4f1a6c935"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_process_events_migration.py -v`
Expected: FAIL (collection or first test) because the migration file does not exist (`FileNotFoundError`).

- [ ] **Step 3: Add the model**

In `Storage/models.py`, directly after the `ImageCut` class, add:

```python
class ProcessEvent(Base):
    """One outcome of a process on one subject, kept forever (epic screenshot-recut, ADR-002). Append-only:
    rates are computed from these rows at read time. A subject has each event at most once, so recording is
    idempotent. batch_run_id is the run that produced the subject; if that run is pruned the event stays,
    unattributed. subject_id has no foreign key because subjects differ per process (image_cuts.id for recut)."""
    __tablename__ = "process_events"
    __table_args__ = (
        UniqueConstraint("process", "event", "subject_id", name="uq_process_events_subject_event"),
        Index("ix_process_events_process_run_event", "process", "batch_run_id", "event"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    process = Column(String(40), nullable=False)
    event = Column(String(40), nullable=False)
    batch_run_id = Column(UUID(as_uuid=True), ForeignKey("batch_runs.run_id", ondelete="SET NULL"), nullable=True)
    subject_id = Column(UUID(as_uuid=True), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

- [ ] **Step 4: Add the migration**

Create `Storage/alembic/versions/c5e2a9d7b140_add_process_events.py`:

```python
"""add process_events

Revision ID: c5e2a9d7b140
Revises: b8d4f1a6c935
Create Date: 2026-10-18 12:00:00.000000

Epic screenshot-recut, project M1 (ADR-002). Append-only table of process outcomes; rates are computed at
read time. batch_run_id is SET NULL so pruning old batch runs never deletes events.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c5e2a9d7b140'
down_revision: Union[str, Sequence[str], None] = 'b8d4f1a6c935'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'process_events',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('process', sa.String(length=40), nullable=False),
        sa.Column('event', sa.String(length=40), nullable=False),
        sa.Column('batch_run_id', sa.UUID(), nullable=True),
        sa.Column('subject_id', sa.UUID(), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_run_id'], ['batch_runs.run_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('process', 'event', 'subject_id', name='uq_process_events_subject_event'),
    )
    op.create_index('ix_process_events_process_run_event', 'process_events', ['process', 'batch_run_id', 'event'])


def downgrade() -> None:
    op.drop_index('ix_process_events_process_run_event', table_name='process_events')
    op.drop_table('process_events')
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_process_events_migration.py -v`
Expected: 5 passed. If the shape comparison differs only by how Postgres renders an equivalent value (for example the unique constraint also listed as an index), adjust the normalisation in `_shape` and add a ledger ruling; do not weaken the negative controls.

Also confirm a single head: `cd Storage && ../.venv311/Scripts/alembic heads` prints only `c5e2a9d7b140 (head)` (run with the environment variables the repo's alembic setup needs; if it needs a live DB URL, use the test database URL only).

- [ ] **Step 6: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/c5e2a9d7b140_add_process_events.py tests/integration/test_process_events_migration.py
git commit -m "feat(storage): process_events table for business metrics

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Repository, registry and `BusinessMetricsService`

**Files:**
- Create: `repository/process_events.py`
- Create: `Backend/app/services/business_metrics_service.py`
- Test: `tests/integration/test_business_metrics_service.py`

**Interfaces:**
- Consumes: `Storage.models.ProcessEvent`, `BatchRun`, `RunStatus` (Task 1 and existing).
- Produces:
  - `repository.process_events.insert_event(session, process, event, batch_run_id, subject_id) -> bool` (True if a row was inserted).
  - `repository.process_events.latest_runs_with_events(session, process, limit) -> list[tuple[uuid.UUID, datetime]]` (newest first).
  - `repository.process_events.count_events(session, process, run_ids) -> list[tuple[uuid.UUID | None, str, int]]` (rows for the given runs plus `None`).
  - `Backend.app.services.business_metrics_service`: `ProcessDefinition`, `PROCESSES`, `UnknownProcessError(ValueError)`, `UnknownEventError(ValueError)`, `BusinessMetricsService(session)` with `async record(process, event, batch_run_id, subject_id) -> bool` and `async batch_metrics(process, limit=50) -> dict`.
  - `batch_metrics` returns `{"process": str, "items": [{"run_id": str, "run_created_at": datetime, "counts": {event: int}, "metrics": {name: float | int | None}}], "unattributed": {"counts": ..., "metrics": ...} | None}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_business_metrics_service.py`:

```python
"""BusinessMetricsService and repository/process_events.py. Requires a live PostgreSQL instance."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from Backend.app.services.business_metrics_service import (
    PROCESSES, BusinessMetricsService, UnknownEventError, UnknownProcessError,
)
from Storage.models import BatchRun, RunStatus


async def _run(session, day):
    run = BatchRun(kind="detect_screenshot_cuts", trigger="manual", status=str(RunStatus.completed),
                   created_at=datetime(2026, 10, day, tzinfo=timezone.utc))
    session.add(run)
    await session.flush()
    return run


async def _record(service, event, run, count=1):
    for _ in range(count):
        await service.record("recut", event, run.run_id if run else None, uuid.uuid4())


@pytest.mark.asyncio(loop_scope="session")
async def test_record_is_idempotent_per_subject_and_event(db_session):
    service = BusinessMetricsService(db_session)
    run, subject = await _run(db_session, 1), uuid.uuid4()

    assert await service.record("recut", "proposed", run.run_id, subject) is True
    assert await service.record("recut", "proposed", run.run_id, subject) is False

    assert (await service.batch_metrics("recut"))["items"][0]["counts"]["proposed"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_record_rejects_an_unknown_process_or_event(db_session):
    service = BusinessMetricsService(db_session)
    with pytest.raises(UnknownProcessError):
        await service.record("nope", "proposed", None, uuid.uuid4())
    with pytest.raises(UnknownEventError):
        await service.record("recut", "typo", None, uuid.uuid4())
    with pytest.raises(UnknownProcessError):
        await service.batch_metrics("nope")


@pytest.mark.asyncio(loop_scope="session")
async def test_record_rolls_back_with_the_callers_transaction(db_session):
    service = BusinessMetricsService(db_session)
    run = await _run(db_session, 1)

    nested = await db_session.begin_nested()
    await service.record("recut", "proposed", run.run_id, uuid.uuid4())
    await nested.rollback()

    assert (await service.batch_metrics("recut"))["items"] == []


@pytest.mark.asyncio(loop_scope="session")
async def test_counts_and_rates_per_run(db_session):
    service = BusinessMetricsService(db_session)
    first, second = await _run(db_session, 1), await _run(db_session, 2)
    await _record(service, "proposed", first, 4)
    await _record(service, "approved", first, 1)
    await _record(service, "rejected", first, 1)
    await _record(service, "proposed", second, 1)

    items = {item["run_id"]: item for item in (await service.batch_metrics("recut"))["items"]}

    one = items[str(first.run_id)]
    assert one["counts"] == {"proposed": 4, "approved": 1, "rejected": 1}
    assert one["metrics"] == {"approve_rate": 0.5, "decided_share": 0.5, "pending": 2}
    two = items[str(second.run_id)]
    assert two["counts"] == {"proposed": 1, "approved": 0, "rejected": 0}
    assert two["metrics"] == {"approve_rate": None, "decided_share": 0.0, "pending": 1}


@pytest.mark.asyncio(loop_scope="session")
async def test_a_late_decision_counts_toward_the_proposing_run(db_session):
    service = BusinessMetricsService(db_session)
    proposing, newer = await _run(db_session, 1), await _run(db_session, 2)
    subject = uuid.uuid4()
    await service.record("recut", "proposed", proposing.run_id, subject)
    await _record(service, "proposed", newer)
    await service.record("recut", "approved", proposing.run_id, subject)

    items = {item["run_id"]: item for item in (await service.batch_metrics("recut"))["items"]}

    assert items[str(proposing.run_id)]["counts"]["approved"] == 1
    assert items[str(newer.run_id)]["counts"]["approved"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_events_without_a_run_are_unattributed_and_runs_without_events_are_omitted(db_session):
    service = BusinessMetricsService(db_session)
    await _run(db_session, 1)  # no events: must not appear
    assert (await service.batch_metrics("recut")) == {"process": "recut", "items": [], "unattributed": None}

    await _record(service, "proposed", None, 2)
    result = await service.batch_metrics("recut")

    assert result["items"] == []
    assert result["unattributed"]["counts"] == {"proposed": 2, "approved": 0, "rejected": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_deleting_a_run_keeps_its_events_as_unattributed(db_session):
    service = BusinessMetricsService(db_session)
    run = await _run(db_session, 1)
    await _record(service, "proposed", run, 3)

    await db_session.execute(delete(BatchRun).where(BatchRun.run_id == run.run_id))

    result = await service.batch_metrics("recut")
    assert result["items"] == []
    assert result["unattributed"]["counts"]["proposed"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_limit_keeps_the_newest_runs_and_still_reports_unattributed(db_session):
    service = BusinessMetricsService(db_session)
    runs = [await _run(db_session, day) for day in (1, 2, 3)]
    for run in runs:
        await _record(service, "proposed", run)
    await _record(service, "proposed", None)

    result = await service.batch_metrics("recut", limit=2)

    assert [item["run_id"] for item in result["items"]] == [str(runs[2].run_id), str(runs[1].run_id)]
    assert result["unattributed"]["counts"]["proposed"] == 1


def test_the_recut_formulas():
    metrics = PROCESSES["recut"].metrics
    counts = {"proposed": 10, "approved": 3, "rejected": 1}
    assert metrics["approve_rate"](counts) == 0.75
    assert metrics["decided_share"](counts) == 0.4
    assert metrics["pending"](counts) == 6


@pytest.mark.parametrize("name", sorted(PROCESSES))
def test_every_metric_survives_all_zero_counts(name):
    definition = PROCESSES[name]
    counts = {event: 0 for event in definition.events}
    for metric in definition.metrics.values():
        value = metric(counts)  # must not raise ZeroDivisionError
        assert value is None or isinstance(value, (int, float))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_business_metrics_service.py -v`
Expected: collection ERROR `ModuleNotFoundError: No module named 'Backend.app.services.business_metrics_service'`. (If importing `Backend.app.services...` from the integration root fails for another reason, record a ledger ruling and fix the import path, not the design.)

- [ ] **Step 3: Write the repository**

Create `repository/process_events.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import BatchRun, ProcessEvent


async def insert_event(session: AsyncSession, process: str, event: str,
                       batch_run_id: uuid.UUID | None, subject_id: uuid.UUID) -> bool:
    """Insert one event; a subject already carrying this event is left alone. Returns True if a row was added.
    Never commits."""
    statement = (
        pg_insert(ProcessEvent)
        .values(process=process, event=event, batch_run_id=batch_run_id, subject_id=subject_id)
        .on_conflict_do_nothing(constraint="uq_process_events_subject_event")
    )
    result = await session.execute(statement)
    return result.rowcount == 1


async def latest_runs_with_events(session: AsyncSession, process: str, limit: int) -> list[tuple[uuid.UUID, datetime]]:
    """The newest `limit` batch runs that have at least one event of this process, newest first."""
    statement = (
        select(BatchRun.run_id, BatchRun.created_at)
        .join(ProcessEvent, ProcessEvent.batch_run_id == BatchRun.run_id)
        .where(ProcessEvent.process == process)
        .group_by(BatchRun.run_id, BatchRun.created_at)
        .order_by(BatchRun.created_at.desc())
        .limit(limit)
    )
    return [(row.run_id, row.created_at) for row in (await session.execute(statement)).all()]


async def count_events(session: AsyncSession, process: str,
                       run_ids: list[uuid.UUID]) -> list[tuple[uuid.UUID | None, str, int]]:
    """Event counts grouped by (run, event) for the given runs, plus the unattributed rows (run None)."""
    statement = (
        select(ProcessEvent.batch_run_id, ProcessEvent.event, func.count())
        .where(ProcessEvent.process == process)
        .where(or_(ProcessEvent.batch_run_id.in_(run_ids), ProcessEvent.batch_run_id.is_(None)))
        .group_by(ProcessEvent.batch_run_id, ProcessEvent.event)
    )
    return [(row[0], row[1], row[2]) for row in (await session.execute(statement)).all()]
```

- [ ] **Step 4: Write the registry and service**

Create `Backend/app/services/business_metrics_service.py`:

```python
"""Business metrics (epic screenshot-recut, ADR-002): record process outcomes and report counts and rates per batch.
Rates are computed here at read time and never stored. Importable by batch code as well as the API."""
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from repository import process_events


class UnknownProcessError(ValueError):
    pass


class UnknownEventError(ValueError):
    pass


Metric = Callable[[Mapping[str, int]], float | int | None]


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


@dataclass(frozen=True)
class ProcessDefinition:
    name: str
    events: tuple[str, ...]
    metrics: Mapping[str, Metric]


PROCESSES: dict[str, ProcessDefinition] = {
    "recut": ProcessDefinition(
        name="recut",
        events=("proposed", "approved", "rejected"),
        metrics={
            # Pending proposals are reported separately and are never counted as rejections.
            "approve_rate": lambda c: _ratio(c["approved"], c["approved"] + c["rejected"]),
            "decided_share": lambda c: _ratio(c["approved"] + c["rejected"], c["proposed"]),
            "pending": lambda c: c["proposed"] - c["approved"] - c["rejected"],
        },
    ),
}


def _definition(process: str) -> ProcessDefinition:
    try:
        return PROCESSES[process]
    except KeyError:
        raise UnknownProcessError(process) from None


def _bucket(definition: ProcessDefinition, event_counts: Mapping[str, int]) -> dict:
    counts = {event: event_counts.get(event, 0) for event in definition.events}
    return {"counts": counts, "metrics": {name: metric(counts) for name, metric in definition.metrics.items()}}


class BusinessMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, process: str, event: str, batch_run_id: uuid.UUID | None, subject_id: uuid.UUID) -> bool:
        """Record one outcome in the caller's transaction (never commits). Returns False if this subject already
        has this event, so a retry cannot double-count."""
        definition = _definition(process)
        if event not in definition.events:
            raise UnknownEventError(f"{process}: {event}")
        return await process_events.insert_event(self._session, process, event, batch_run_id, subject_id)

    async def batch_metrics(self, process: str, limit: int = 50) -> dict:
        """Counts and rates for the newest `limit` runs that have events, newest first, plus the unattributed
        rows (events whose run was pruned or never known) or None when there are none."""
        definition = _definition(process)
        runs = await process_events.latest_runs_with_events(self._session, process, limit)
        rows = await process_events.count_events(self._session, process, [run_id for run_id, _ in runs])

        per_run: dict[uuid.UUID, dict[str, int]] = {}
        unattributed: dict[str, int] = {}
        for run_id, event, count in rows:
            target = unattributed if run_id is None else per_run.setdefault(run_id, {})
            target[event] = count

        items = [
            {"run_id": str(run_id), "run_created_at": created_at, **_bucket(definition, per_run.get(run_id, {}))}
            for run_id, created_at in runs
        ]
        return {
            "process": process,
            "items": items,
            "unattributed": _bucket(definition, unattributed) if unattributed else None,
        }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_business_metrics_service.py -v`
Expected: all pass (10 tests including the parametrized guard).

- [ ] **Step 6: Run the whole integration root (shared models changed in Task 1)**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/ -q`
Expected: all pass (the 618 from before plus the new ones).

- [ ] **Step 7: Commit**

```bash
git add repository/process_events.py Backend/app/services/business_metrics_service.py tests/integration/test_business_metrics_service.py
git commit -m "feat(metrics): BusinessMetricsService records and reports process outcomes per batch

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---
### Task 3: Metrics endpoint, shared schema and generated types

**Files:**
- Modify: `Backend/app/api/admin.py`
- Create: `shared/schemas/batchmetricsbucket.schema.json`, `shared/schemas/batchmetricsitem.schema.json`, `shared/schemas/batchmetricsresponse.schema.json`
- Modify: `shared/schemas/all.schema.json` (three entries after `BatchNamesResponse`)
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `Backend/app/types/generated/*`, Kotlin DTOs under `AndroidClient/`
- Modify: `backend_api.md` (new section after "List Batch Runs")
- Test: `Backend/tests/test_admin_metrics_endpoint.py`

**Interfaces:**
- Consumes: `BusinessMetricsService`, `UnknownProcessError` (Task 2).
- Produces: `GET /api/admin/batches/metrics?process=<name>&limit=<1..200, default 50>` returning `BatchMetricsResponse` = `{process: str, items: [BatchMetricsItem], unattributed: BatchMetricsBucket | null}`; `BatchMetricsItem` = `{run_id: str, run_created_at: date-time str, counts: {str: int}, metrics: {str: number | null}}`; `BatchMetricsBucket` = `{counts, metrics}`. Dependency `get_business_metrics_service` in `Backend.app.api.admin`. Unknown process: 404. Task 4 consumes the generated TS types `BatchMetricsResponse`, `BatchMetricsItem`, `BatchMetricsBucket`.

- [ ] **Step 1: Write the failing endpoint tests**

Create `Backend/tests/test_admin_metrics_endpoint.py`:

```python
"""Tests for GET /api/admin/batches/metrics (service mocked, like test_admin_batch_endpoints.py)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from Backend.app.api.admin import get_business_metrics_service, router as admin_router
from Backend.app.services.business_metrics_service import UnknownProcessError

app = FastAPI()
app.include_router(admin_router, prefix="/api")

PAYLOAD = {
    "process": "recut",
    "items": [{
        "run_id": "b3f1c2a4-0000-0000-0000-000000000001",
        "run_created_at": datetime(2026, 10, 18, 9, 0, tzinfo=timezone.utc),
        "counts": {"proposed": 4, "approved": 1, "rejected": 1},
        "metrics": {"approve_rate": 0.5, "decided_share": 0.5, "pending": 2},
    }],
    "unattributed": None,
}


@pytest.fixture
def mock_service():
    return AsyncMock()


@pytest.fixture
def client(mock_service):
    async def override():
        yield mock_service

    app.dependency_overrides[get_business_metrics_service] = override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_returns_the_service_payload(client, mock_service):
    mock_service.batch_metrics.return_value = PAYLOAD

    response = client.get("/api/admin/batches/metrics?process=recut")

    assert response.status_code == 200
    body = response.json()
    assert body["process"] == "recut" and body["unattributed"] is None
    assert body["items"][0]["metrics"] == {"approve_rate": 0.5, "decided_share": 0.5, "pending": 2}
    assert body["items"][0]["run_created_at"].startswith("2026-10-18T09:00:00")
    mock_service.batch_metrics.assert_awaited_once_with("recut", limit=50)


def test_passes_the_limit_through(client, mock_service):
    mock_service.batch_metrics.return_value = {**PAYLOAD, "items": []}

    client.get("/api/admin/batches/metrics?process=recut&limit=7")

    mock_service.batch_metrics.assert_awaited_once_with("recut", limit=7)


def test_serialises_null_rates_and_a_present_unattributed_bucket(client, mock_service):
    bucket = {"counts": {"proposed": 2, "approved": 0, "rejected": 0},
              "metrics": {"approve_rate": None, "decided_share": 0.0, "pending": 2}}
    mock_service.batch_metrics.return_value = {**PAYLOAD, "items": [], "unattributed": bucket}

    body = client.get("/api/admin/batches/metrics?process=recut").json()

    assert body["unattributed"] == bucket


def test_unknown_process_is_404(client, mock_service):
    mock_service.batch_metrics.side_effect = UnknownProcessError("nope")

    assert client.get("/api/admin/batches/metrics?process=nope").status_code == 404


@pytest.mark.parametrize("query", ["process=recut&limit=0", "process=recut&limit=201", "limit=10"])
def test_limit_out_of_range_is_422(client, query):
    assert client.get(f"/api/admin/batches/metrics?{query}").status_code == 422
```

- [ ] **Step 2: Run them to verify they fail**

Run (from `Backend/`): `../.venv311/Scripts/python -m pytest tests/test_admin_metrics_endpoint.py -v`
Expected: collection ERROR `ImportError: cannot import name 'get_business_metrics_service'`.

- [ ] **Step 3: Add the router code**

In `Backend/app/api/admin.py`: extend the imports and add models, dependency and route. Imports become:

```python
from fastapi import APIRouter, Depends, HTTPException, Query
...
from Backend.app.services.business_metrics_service import BusinessMetricsService, UnknownProcessError
```

Add after `BatchNamesResponse`:

```python
class BatchMetricsBucket(BaseModel):
    counts: dict[str, int]
    metrics: dict[str, Optional[float]]


class BatchMetricsItem(BatchMetricsBucket):
    run_id: str
    run_created_at: datetime


class BatchMetricsResponse(BaseModel):
    process: str
    items: list[BatchMetricsItem]
    unattributed: Optional[BatchMetricsBucket]
```

Add after `get_admin_batch_service`:

```python
async def get_business_metrics_service(
    db=Depends(get_async_db),
) -> AsyncGenerator[BusinessMetricsService, None]:
    yield BusinessMetricsService(db)
```

Add the route (a `GET` with a single static segment, so it does not clash with `/{batch_name}/run` or `/runs/{run_id}`):

```python
@router.get("/metrics", response_model=BatchMetricsResponse)
async def batch_metrics(
    process: str,
    limit: int = Query(50, ge=1, le=200),
    service: BusinessMetricsService = Depends(get_business_metrics_service),
):
    try:
        return await service.batch_metrics(process, limit=limit)
    except UnknownProcessError:
        raise HTTPException(status_code=404, detail=f"Unknown process: {process}")
```

- [ ] **Step 4: Run the endpoint tests, then the Backend root**

Run (from `Backend/`): `../.venv311/Scripts/python -m pytest tests/test_admin_metrics_endpoint.py -v` then `../.venv311/Scripts/python -m pytest -q`
Expected: 7 passed, then the whole Backend root passes.

- [ ] **Step 5: Add the shared schemas**

Create `shared/schemas/batchmetricsbucket.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "batchmetricsbucket.schema.json",
  "title": "BatchMetricsBucket",
  "type": "object",
  "properties": {
    "counts": { "type": "object", "additionalProperties": { "type": "integer" }, "description": "Events counted per event name; every event the process defines is present" },
    "metrics": { "type": "object", "additionalProperties": { "type": ["number", "null"] }, "description": "Computed at read time; null when the denominator is zero. Names ending in _rate or _share are fractions (0..1)" }
  },
  "required": ["counts", "metrics"]
}
```

Create `shared/schemas/batchmetricsitem.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "batchmetricsitem.schema.json",
  "title": "BatchMetricsItem",
  "type": "object",
  "properties": {
    "run_id": { "type": "string", "description": "The batch run (detection run) that produced the subjects counted here" },
    "run_created_at": { "type": "string", "format": "date-time" },
    "counts": { "type": "object", "additionalProperties": { "type": "integer" } },
    "metrics": { "type": "object", "additionalProperties": { "type": ["number", "null"] } }
  },
  "required": ["run_id", "run_created_at", "counts", "metrics"]
}
```

Create `shared/schemas/batchmetricsresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "batchmetricsresponse.schema.json",
  "title": "BatchMetricsResponse",
  "type": "object",
  "properties": {
    "process": { "type": "string" },
    "items": { "type": "array", "items": { "$ref": "./batchmetricsitem.schema.json" }, "description": "Newest run first; runs without events are omitted" },
    "unattributed": {
      "oneOf": [{ "type": "null" }, { "$ref": "./batchmetricsbucket.schema.json" }],
      "description": "Events whose batch run was pruned or never known; null when there are none"
    }
  },
  "required": ["process", "items", "unattributed"]
}
```

In `shared/schemas/all.schema.json`, after the `BatchNamesResponse` line add:

```json
    "BatchMetricsBucket":      { "$ref": "batchmetricsbucket.schema.json" },
    "BatchMetricsItem":        { "$ref": "batchmetricsitem.schema.json" },
    "BatchMetricsResponse":    { "$ref": "batchmetricsresponse.schema.json" },
```

- [ ] **Step 6: Regenerate the three type trees**

Run, in this order (commands from `documents/generation.md` and `CLAUDE.md`):
- TypeScript, from `Frontend/`: `bash generate-types.sh`
- Python, from `Backend/`: `../.venv311/Scripts/datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel`
- Kotlin, from the repo root: `.venv311/Scripts/python AndroidClient/scripts/generate_dtos.py`

Expected: new `batchmetrics*.py` files, new entries in `Backend/app/types/generated/__init__.py` and `all.d.ts`, and Kotlin DTOs for the three types. The Python generator rewrites a timestamp comment in every file: run `git diff --numstat Backend/app/types/generated` and `git checkout -- <file>` for each file whose numstat is `1 1` (timestamp only). Then check `git diff --stat` shows only additions related to the three new types. If the Kotlin script fails for an unrelated pre-existing reason, record a ledger ruling and leave Kotlin out; do not hand-write DTOs.

- [ ] **Step 7: Document the endpoint**

In `backend_api.md`, after the "List Batch Runs" section's closing paragraph ("Only runs of the admin-triggerable kinds ...") and before the `---`, add:

````markdown
#### Batch Metrics

- **URL**: `/api/admin/batches/metrics`
- **Method**: `GET`
- **Query params**: `process` (required; today `recut`), `limit` (number of runs, `1` to `200`, default `50`)
- **Response**: `BatchMetricsResponse`; unknown `process` is `404`, a `limit` out of range is `422`
- **Example**: `GET /api/admin/batches/metrics?process=recut&limit=10`

```json
{
  "process": "recut",
  "items": [
    {
      "run_id": "b3f1c2a4-...",
      "run_created_at": "2026-10-18T09:00:00Z",
      "counts": { "proposed": 4, "approved": 1, "rejected": 1 },
      "metrics": { "approve_rate": 0.5, "decided_share": 0.5, "pending": 2 }
    }
  ],
  "unattributed": null
}
```

One item per detection run that has at least one event, newest run first. A decision is attributed to the run that
produced the proposal, however late it arrives. Rates are computed on every request and never stored:
`approve_rate` = approved / (approved + rejected), `decided_share` = (approved + rejected) / proposed, `pending` =
proposed - approved - rejected (pending proposals are not counted as rejections). A rate is `null` when its
denominator is zero. `unattributed` holds events whose batch run was pruned or never known, or `null` when there are
none. Events come from the process code (screenshot recut: projects R3 and R5), so the list is empty until they ship.
````

- [ ] **Step 8: Verify nothing else moved, then commit**

Run: `.venv311/Scripts/python -m pytest tests/docs/ -q` (from the repo root) and, from `Backend/`, `../.venv311/Scripts/python -m pytest -q`.
Expected: both green.

```bash
git add Backend/app/api/admin.py Backend/tests/test_admin_metrics_endpoint.py shared/schemas Frontend/memes-frontend/src/types/generated Backend/app/types/generated AndroidClient backend_api.md
git commit -m "feat(api): GET /api/admin/batches/metrics with shared schema and generated types

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

(`git add AndroidClient` only picks up the generated DTO files; check `git status` first and add exact paths if anything unrelated shows.)

---

### Task 4: `BusinessMetricsPanel` on `/admin/batches`

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, `Frontend/memes-frontend/src/test/mockApi.ts`
- Create: `Frontend/memes-frontend/src/components/BusinessMetricsPanel.tsx`
- Modify: `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`
- Test: `Frontend/memes-frontend/src/components/BusinessMetricsPanel.test.tsx`, `Frontend/memes-frontend/src/pages/AdminBatchesPage.test.tsx`

**Interfaces:**
- Consumes: generated TS `BatchMetricsResponse`, `BatchMetricsItem`, `BatchMetricsBucket` and the endpoint from Task 3.
- Produces: `MemesApi.getBatchMetrics(process: string, limit?: number): Promise<BatchMetricsResponse>`; component `BusinessMetricsPanel` with props `{ memesApi: MemesApi; process: string; title: string; emptyText: string }`.

- [ ] **Step 1: Write the failing panel tests**

Create `Frontend/memes-frontend/src/components/BusinessMetricsPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import BusinessMetricsPanel from './BusinessMetricsPanel'
import { makeMockApi } from '../test/mockApi'
import type { BatchMetricsItem, BatchMetricsResponse } from '../types/generated/all'

function makeItem(overrides: Partial<BatchMetricsItem> = {}): BatchMetricsItem {
  return {
    run_id: 'run-1',
    run_created_at: '2026-10-18T09:00:00Z',
    counts: { proposed: 4, approved: 1, rejected: 1 },
    metrics: { approve_rate: 0.5, decided_share: 0.5, pending: 2 },
    ...overrides,
  }
}

function makeResponse(overrides: Partial<BatchMetricsResponse> = {}): BatchMetricsResponse {
  return { process: 'recut', items: [makeItem()], unattributed: null, ...overrides }
}

function renderPanel(getBatchMetrics: ReturnType<typeof vi.fn>) {
  const api = makeMockApi({ getBatchMetrics })
  render(<BusinessMetricsPanel memesApi={api} process="recut" title="Recut outcomes" emptyText="No recut proposals yet" />)
  return api
}

describe('BusinessMetricsPanel', () => {
  it('renders one row per run with counts and percentages', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse()))

    await waitFor(() => expect(screen.getByText('Recut outcomes')).toBeInTheDocument())
    expect(await screen.findAllByText('50.0%')).toHaveLength(2) // approve rate and decided share
    expect(screen.getByText('Approve rate')).toBeInTheDocument()
    expect(screen.getByText('Decided share')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument() // proposed
    expect(screen.getByText('2')).toBeInTheDocument() // pending, shown as a count not a percentage
    expect(screen.getByText(/not counted as rejections/i)).toBeInTheDocument()
  })

  it('shows a dash for a missing rate', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse({
      items: [makeItem({
        counts: { proposed: 1, approved: 0, rejected: 0 },
        metrics: { approve_rate: null, decided_share: 0, pending: 1 },
      })],
    })))

    expect(await screen.findByText('-')).toBeInTheDocument()
    expect(screen.getByText('0.0%')).toBeInTheDocument()
  })

  it('shows an unattributed row when events lost their run', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse({
      items: [],
      unattributed: { counts: { proposed: 3, approved: 0, rejected: 0 }, metrics: { approve_rate: null, decided_share: 0, pending: 3 } },
    })))

    expect(await screen.findByText('Unattributed')).toBeInTheDocument()
  })

  it('shows the empty text when there are no events', async () => {
    renderPanel(vi.fn().mockResolvedValue(makeResponse({ items: [] })))

    expect(await screen.findByText('No recut proposals yet')).toBeInTheDocument()
  })

  it('shows an error when the fetch fails', async () => {
    renderPanel(vi.fn().mockRejectedValue(new Error('boom')))

    expect(await screen.findByText('boom')).toBeInTheDocument()
  })

  it('asks for the process it was given', async () => {
    const getBatchMetrics = vi.fn().mockResolvedValue(makeResponse())
    renderPanel(getBatchMetrics)

    await waitFor(() => expect(getBatchMetrics).toHaveBeenCalledWith('recut'))
  })
})
```

- [ ] **Step 2: Run them to verify they fail**

Run (from `Frontend/memes-frontend/`): `pnpm vitest run src/components/BusinessMetricsPanel.test.tsx`
Expected: FAIL, `Failed to resolve import "./BusinessMetricsPanel"` (and the type error on `getBatchMetrics` until the API exists).

- [ ] **Step 3: Add the API method**

In `src/api/MemesApi.ts`: add `BatchMetricsResponse` to the type import list on line 6's import (next to `BatchNamesResponse`) and, after `listBatchRuns(...)` in the interface, add:

```ts
  getBatchMetrics(process: string, limit?: number): Promise<BatchMetricsResponse>;
```

In `src/api/http/HttpMemesApi.ts`: add `BatchMetricsResponse` to the same generated-types import, and after `listBatchRuns` add (copy the error style of the neighbouring method; read it first and keep its headers and error format):

```ts
  async getBatchMetrics(process: string, limit?: number): Promise<BatchMetricsResponse> {
    const params = new URLSearchParams({ process })
    if (limit !== undefined) params.set("limit", String(limit))
    const res = await fetch(`${this.baseUrl}/api/admin/batches/metrics?${params.toString()}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch batch metrics: ${res.status}`)
    return res.json()
  }
```

In `src/test/mockApi.ts`, after the `listBatchRuns` default add:

```ts
    getBatchMetrics: vi.fn().mockResolvedValue({ process: 'recut', items: [], unattributed: null }),
```

- [ ] **Step 4: Write the component**

Create `src/components/BusinessMetricsPanel.tsx`:

```tsx
import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { BatchMetricsBucket, BatchMetricsResponse } from "../types/generated/all"

type Props = { memesApi: MemesApi; process: string; title: string; emptyText: string }

type State =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: BatchMetricsResponse }

function label(key: string): string {
  const spaced = key.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

// A null metric means its denominator was zero. Names ending in _rate or _share are fractions.
function formatMetric(key: string, value: number | null): string {
  if (value === null) return "-"
  return /_(rate|share)$/.test(key) ? `${(value * 100).toFixed(1)}%` : String(value)
}

function Cells({ bucket, countKeys, metricKeys }: { bucket: BatchMetricsBucket; countKeys: string[]; metricKeys: string[] }) {
  return (
    <>
      {countKeys.map((key) => <td key={`c-${key}`}>{bucket.counts[key] ?? 0}</td>)}
      {metricKeys.map((key) => <td key={`m-${key}`}>{formatMetric(key, bucket.metrics[key] ?? null)}</td>)}
    </>
  )
}

export default function BusinessMetricsPanel({ memesApi, process, title, emptyText }: Props) {
  const [state, setState] = useState<State>({ status: "loading" })

  useEffect(() => {
    let cancelled = false
    // `await Promise.resolve()` keeps the first setState out of the synchronous effect body, same as
    // AdminBatchesPage.tsx (react-hooks/set-state-in-effect).
    void (async () => {
      await Promise.resolve()
      try {
        const data = await memesApi.getBatchMetrics(process)
        if (!cancelled) setState({ status: "ready", data })
      } catch (e: unknown) {
        if (!cancelled) setState({ status: "error", message: e instanceof Error ? e.message : "Failed to load metrics" })
      }
    })()
    return () => { cancelled = true }
  }, [memesApi, process])

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <h2 className="text-lg font-semibold mb-2">{title}</h2>
      <Body state={state} emptyText={emptyText} />
    </div>
  )
}

function Body({ state, emptyText }: { state: State; emptyText: string }) {
  if (state.status === "loading") return <p className="text-sm text-gray-400">Loading…</p>
  if (state.status === "error") return <p className="text-sm text-red-500">{state.message}</p>

  const { items, unattributed } = state.data
  const sample = items[0] ?? unattributed
  if (!sample) return <p className="text-sm text-gray-400">{emptyText}</p>
  const countKeys = Object.keys(sample.counts)
  const metricKeys = Object.keys(sample.metrics)

  return (
    <>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-gray-500 border-b">
            <th className="py-1">Run started</th>
            {countKeys.map((key) => <th key={`c-${key}`}>{label(key)}</th>)}
            {metricKeys.map((key) => <th key={`m-${key}`}>{label(key)}</th>)}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.run_id} className="border-b last:border-0">
              <td className="py-1">{item.run_created_at}</td>
              <Cells bucket={item} countKeys={countKeys} metricKeys={metricKeys} />
            </tr>
          ))}
          {unattributed && (
            <tr className="border-b last:border-0">
              <td className="py-1">Unattributed</td>
              <Cells bucket={unattributed} countKeys={countKeys} metricKeys={metricKeys} />
            </tr>
          )}
        </tbody>
      </table>
      <p className="text-xs text-gray-500 mt-2">Pending proposals are not counted as rejections.</p>
    </>
  )
}
```

- [ ] **Step 5: Run the panel tests**

Run: `pnpm vitest run src/components/BusinessMetricsPanel.test.tsx`
Expected: 6 passed. If `getByText('4')` or `getByText('2')` finds several cells, make the test query specific (for example by row) without changing what it proves; ledger a ruling.

- [ ] **Step 6: Write the failing page tests**

Append to the `describe('AdminBatchesPage', ...)` block in `src/pages/AdminBatchesPage.test.tsx`:

```tsx
  it('shows the recut outcomes panel', async () => {
    const api = makeMockApi({ listBatchRuns: vi.fn().mockResolvedValue(makeList([])) })
    render(<AdminBatchesPage memesApi={api} />)

    expect(await screen.findByText('Recut outcomes')).toBeInTheDocument()
    expect(api.getBatchMetrics).toHaveBeenCalledWith('recut')
  })

  it('a failing metrics fetch leaves the page usable', async () => {
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([])),
      getBatchMetrics: vi.fn().mockRejectedValue(new Error('metrics down')),
    })
    render(<AdminBatchesPage memesApi={api} />)

    expect(await screen.findByText('metrics down')).toBeInTheDocument()
    expect(screen.getByText('move_flagged')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Run' }).length).toBeGreaterThan(0)
  })
```

Run: `pnpm vitest run src/pages/AdminBatchesPage.test.tsx`
Expected: the two new tests FAIL (`Unable to find an element with the text: Recut outcomes`); existing tests still pass.

- [ ] **Step 7: Mount the panel**

In `src/pages/AdminBatchesPage.tsx`: add `import BusinessMetricsPanel from "../components/BusinessMetricsPanel"` beside the `DuplicateDecisionsPanel` import, and render it between the runs table card and `DuplicateDecisionsPanel`:

```tsx
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="recut"
        title="Recut outcomes"
        emptyText="No recut proposals yet"
      />

      <DuplicateDecisionsPanel memesApi={memesApi} />
```

- [ ] **Step 8: Run the frontend gate**

From `Frontend/memes-frontend/`: `pnpm vitest run`, `tsc -b`, `pnpm eslint src/ --max-warnings 0`
Expected: all green, 0 warnings. Also `git diff Frontend/memes-frontend/src/types/generated/` shows nothing new beyond Task 3's commit.

- [ ] **Step 9: Commit**

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(frontend): recut outcomes panel on /admin/batches

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Docs and status

**Files:**
- Modify: `docs/batches/execution-model.md` (new short section), `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-18-m1-business-metrics-design.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`

**Interfaces:**
- Consumes: everything above.
- Produces: M1 documented and marked `implementation`.

- [ ] **Step 1: Document the mechanism**

Read `docs/batches/execution-model.md` and append a section `## Business metrics` at the end (match the file's heading level and tone):

```markdown
## Business metrics

`process_events` (one row per process, event and subject, kept forever) records the outcome of a process on one subject, for
example a recut proposal being proposed, approved or rejected. Process code calls `BusinessMetricsService.record(process,
event, batch_run_id, subject_id)` in the same transaction as the state change; recording is idempotent per subject and event.
The allowed processes, events and rate formulas live in the `PROCESSES` registry in
`Backend/app/services/business_metrics_service.py`. Counts and rates per batch run are computed on request by
`GET /api/admin/batches/metrics` and shown as the "Recut outcomes" panel on `/admin/batches`; nothing is stored. A decision is
attributed to the detection run that produced the proposal. Design: `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-18-m1-business-metrics-design.md`.
```

- [ ] **Step 2: Update the status lines**

In the M1 spec change `Status: planned` to `Status: implementation`, and in `tracker.md` change the M1 row's status from `planned` to `implementation` (the Plan link is already there; R1 used the same wording: code complete, review and rollout pending). `done` waits for the merge and the live rollout.

- [ ] **Step 3: Run the guards and commit**

Run: `.venv311/Scripts/python -m pytest tests/docs/ -q`
Expected: 9 passed.

```bash
git add docs/batches/execution-model.md docs/superpowers/specs/2026-10-17-screenshot-recut
git commit -m "docs: business metrics mechanism and M1 status

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
