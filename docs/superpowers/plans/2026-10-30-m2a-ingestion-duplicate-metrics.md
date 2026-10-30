# M2a Ingestion Duplicate Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record `proposed`, `rejected` and `kept` events for the ingestion duplicate review (Tier A and Tier B, per image, per ingestion run) and show them as two panels on `/admin/batches`.

**Architecture:** Two new processes in the M1 registry (`ingest_tier_a`, `ingest_tier_b`) plus `retract` / lookup methods on `BusinessMetricsService`. A new `ingestion_metrics` module wraps every write in a savepoint that logs and swallows failures, and is called from `ingest_find_duplicates` (propose), `IngestionService.resolve` / `undo_reject` (decide, undo) and `ingest_promote.promote` (implicit keeps). The existing metrics endpoint and `BusinessMetricsPanel` serve the new processes; the panel gets an optional `note` prop.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL (`process_events`), React + vitest.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-30-m2a-ingestion-duplicate-metrics-design.md`

## Global Constraints

- Processes `ingest_tier_a`, `ingest_tier_b`; events `proposed`, `rejected`, `kept`; `("rejected", "kept")` is an exclusive group.
- Metrics: `reject_share` = rejected / (rejected + kept); `decided_share` = (rejected + kept) / proposed; `pending` = proposed - rejected - kept; zero denominator gives `None`.
- Subject = the pending image; batch run = `images.ingestion_batch_id`.
- Tier bands: A = `[0, 0.05)`, B = `[0.05, settings.DUPLICATES.THRESHOLD)` (`_tier_band` in `Backend/app/services/ingestion_service.py`).
- Every metrics write in the ingestion code runs in a savepoint (`session.begin_nested()`), logs a warning and swallows any `Exception`; `BaseException` is not caught; none of it commits.
- `record_decision` records only for an image that already has `proposed` in that tier.
- `undo_reject` retracts `rejected` in both tiers. `ingest_promote` records `kept` for promoted images that are proposed and undecided in a tier.
- No migration, no new endpoint, no new shared schema (the batch-metrics schema is a free map: `shared/schemas/batchmetricsbucket.schema.json`).
- Python 3.11 venv by absolute path: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe`. Test DB only: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`. Never use a live `DATABASE_URL`; no live runs.
- Test roots run separately (`tests/integration/`, `Backend/` from its dir, `batch/tests/`, `tests/evaluation/`, `tests/rules/`, `tests/docs/`, vitest). The shared test DB may deadlock under load: rerun, do not "fix". Known unrelated failure: `Backend/tests/test_batch_subprocess.py::...::test_forces_utf8_output_encoding_in_child_env`.
- Worktree guard: use plain single commands (no heredocs, no `git -C`, no compound pipelines with git); create and edit files with the Write/Edit tools. Many files are CRLF: use the Edit tool. Frontend `node_modules`: junction the main checkout's with `cmd /c mklink /J` (or PowerShell `New-Item -ItemType Junction`) and remove the junction with `cmd /c rmdir` (never a recursive delete) when finished.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Nothing is pushed.

## Review Focus

- A metrics write that raises inside `resolve` must leave the decision applied (image rejected / kept) and the response unchanged (Task 3).
- A stale `keep` for an image that was never proposed in that tier (already promoted, or outside the queue) must not create a `kept` (Tasks 2 and 3).
- `undo_reject` followed by `keep` (or a second `reject`) must work and leave exactly one counted outcome (Task 3).
- A pending image of another ingestion run that appears in a candidate row must not be proposed under this run (Task 2).
- A re-run of `ingest_find_duplicates`, or its other tier, must not double-count or propose an image in the wrong tier band (Task 4).
- An image released by the rejection of its partner must end `kept` at promote, once, and a decided image must not get a second outcome (Task 4).

---

### Task 1: Registry, repository and service methods

**Files:**
- Modify: `repository/process_events.py`, `Backend/app/services/business_metrics_service.py`
- Test: `tests/integration/test_business_metrics_service.py`

**Interfaces:**
- Produces:
  - `process_events.delete_event(session, process: str, event: str, subject_id: uuid.UUID) -> bool`
  - `process_events.subjects_with_event(session, process: str, event: str, subject_ids: list[uuid.UUID]) -> set[uuid.UUID]`
  - `BusinessMetricsService.retract(process, event, subject_id) -> bool`
  - `BusinessMetricsService.with_event(process, event, subject_ids) -> set[uuid.UUID]`
  - `BusinessMetricsService.undecided(process, proposed_event, decided_events: tuple[str, ...], subject_ids) -> set[uuid.UUID]`
  - `PROCESSES["ingest_tier_a"]`, `PROCESSES["ingest_tier_b"]`

- [ ] **Step 1: Write the failing tests** (append to `tests/integration/test_business_metrics_service.py`, `@pytest.mark.asyncio(loop_scope="session")`, reuse its `_run` helper and `uuid` import):

```python
@pytest.mark.parametrize("process", ["ingest_tier_a", "ingest_tier_b"])
def test_the_ingestion_formulas(process):
    metrics = PROCESSES[process].metrics
    counts = {"proposed": 10, "rejected": 3, "kept": 1}
    assert metrics["reject_share"](counts) == 0.75
    assert metrics["decided_share"](counts) == 0.4
    assert metrics["pending"](counts) == 6
    zero = {"proposed": 0, "rejected": 0, "kept": 0}
    assert metrics["reject_share"](zero) is None and metrics["decided_share"](zero) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_and_kept_are_exclusive_in_an_ingestion_process(db_session):
    service = BusinessMetricsService(db_session)
    run, subject = await _run(db_session, 1), uuid.uuid4()
    await service.record("ingest_tier_a", "rejected", run.run_id, subject)

    with pytest.raises(ConflictingEventError):
        await service.record("ingest_tier_a", "kept", run.run_id, subject)
    assert await service.record("ingest_tier_b", "kept", run.run_id, subject) is True   # another tier is independent


@pytest.mark.asyncio(loop_scope="session")
async def test_retract_removes_only_that_event_and_is_a_no_op_the_second_time(db_session):
    service = BusinessMetricsService(db_session)
    run, subject = await _run(db_session, 1), uuid.uuid4()
    await service.record("ingest_tier_a", "proposed", run.run_id, subject)
    await service.record("ingest_tier_a", "rejected", run.run_id, subject)

    assert await service.retract("ingest_tier_a", "rejected", subject) is True
    assert await service.retract("ingest_tier_a", "rejected", subject) is False
    assert await service.with_event("ingest_tier_a", "proposed", [subject]) == {subject}
    assert await service.with_event("ingest_tier_a", "rejected", [subject]) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_after_a_retract_the_subject_can_be_kept_or_rejected_again(db_session):
    service = BusinessMetricsService(db_session)
    run, subject = await _run(db_session, 1), uuid.uuid4()
    await service.record("ingest_tier_a", "rejected", run.run_id, subject)
    await service.retract("ingest_tier_a", "rejected", subject)

    assert await service.record("ingest_tier_a", "kept", run.run_id, subject) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_retract_and_lookups_reject_an_unknown_process_or_event(db_session):
    service = BusinessMetricsService(db_session)
    with pytest.raises(UnknownProcessError):
        await service.retract("nope", "rejected", uuid.uuid4())
    with pytest.raises(UnknownEventError):
        await service.with_event("ingest_tier_a", "nope", [uuid.uuid4()])


@pytest.mark.asyncio(loop_scope="session")
async def test_undecided_returns_proposed_subjects_without_a_decision(db_session):
    service = BusinessMetricsService(db_session)
    run = await _run(db_session, 1)
    open_, rejected, kept, never = (uuid.uuid4() for _ in range(4))
    for subject in (open_, rejected, kept):
        await service.record("ingest_tier_a", "proposed", run.run_id, subject)
    await service.record("ingest_tier_a", "rejected", run.run_id, rejected)
    await service.record("ingest_tier_a", "kept", run.run_id, kept)

    result = await service.undecided("ingest_tier_a", "proposed", ("rejected", "kept"), [open_, rejected, kept, never])

    assert result == {open_}
    assert await service.undecided("ingest_tier_a", "proposed", ("rejected", "kept"), []) == set()
```
Check the file's existing imports and add `UnknownEventError`, `UnknownProcessError`, `ConflictingEventError`, `PROCESSES`, `BusinessMetricsService` if missing.

- [ ] **Step 2: Run to verify failure.** `DATABASE_URL=... H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_business_metrics_service.py -q` → FAIL (unknown process / missing methods).

- [ ] **Step 3: Implement.**
  - `repository/process_events.py`: add `delete` to the sqlalchemy import and

```python
async def delete_event(session: AsyncSession, process: str, event: str, subject_id: uuid.UUID) -> bool:
    """Remove one event of one subject (the retraction of an undone decision). Returns True if a row went. Never commits."""
    result = await session.execute(delete(ProcessEvent).where(
        ProcessEvent.process == process, ProcessEvent.event == event, ProcessEvent.subject_id == subject_id))
    return result.rowcount == 1


async def subjects_with_event(session: AsyncSession, process: str, event: str,
                              subject_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of `subject_ids` carry this event."""
    if not subject_ids:
        return set()
    rows = await session.execute(select(ProcessEvent.subject_id).where(
        ProcessEvent.process == process, ProcessEvent.event == event, ProcessEvent.subject_id.in_(subject_ids)))
    return set(rows.scalars())
```
  - `business_metrics_service.py`: add to `PROCESSES` (after `"recut"`):

```python
def _ingestion_process(name: str) -> ProcessDefinition:
    """One ingestion review tier (M2a). Subject = the pending image; `kept` includes the winner of a true duplicate
    group, so `reject_share` is a share of decisions, not a precision."""
    return ProcessDefinition(
        name=name,
        events=("proposed", "rejected", "kept"),
        metrics={
            "reject_share": lambda c: _ratio(c["rejected"], c["rejected"] + c["kept"]),
            "decided_share": lambda c: _ratio(c["rejected"] + c["kept"], c["proposed"]),
            "pending": lambda c: c["proposed"] - c["rejected"] - c["kept"],
        },
        exclusive=(("rejected", "kept"),),
    )
```
and after the `PROCESSES` dict literal: `PROCESSES["ingest_tier_a"] = _ingestion_process("ingest_tier_a")` and `PROCESSES["ingest_tier_b"] = _ingestion_process("ingest_tier_b")` (define `_ingestion_process` above `PROCESSES`). Service methods:

```python
    async def retract(self, process: str, event: str, subject_id: uuid.UUID) -> bool:
        """Remove one event of one subject. Only for the undo of the decision the event describes (ADR-002, M2a).
        Returns False if there was none. Never commits."""
        definition = _definition(process)
        if event not in definition.events:
            raise UnknownEventError(f"{process}: {event}")
        return await process_events.delete_event(self._session, process, event, subject_id)

    async def with_event(self, process: str, event: str, subject_ids) -> set[uuid.UUID]:
        definition = _definition(process)
        if event not in definition.events:
            raise UnknownEventError(f"{process}: {event}")
        return await process_events.subjects_with_event(self._session, process, event, list(subject_ids))

    async def undecided(self, process: str, proposed_event: str, decided_events: tuple[str, ...],
                        subject_ids) -> set[uuid.UUID]:
        """The subjects that carry `proposed_event` and none of `decided_events`."""
        remaining = await self.with_event(process, proposed_event, subject_ids)
        for event in decided_events:
            remaining -= await self.with_event(process, event, remaining)
        return remaining
```
- [ ] **Step 4: Run to verify pass:** the file, then `tests/integration/test_recut_stats.py` and `cd Backend && pytest tests/test_admin_metrics_endpoint.py`.
- [ ] **Step 5: Commit** (`feat(recut): M2a ingestion processes in the metrics registry`).

---

### Task 2: The `ingestion_metrics` module

**Files:**
- Create: `Backend/app/services/ingestion_metrics.py`
- Test: `tests/integration/test_ingestion_metrics.py`

**Interfaces:**
- Consumes: Task 1 service methods; `IngestionRepository.get_tier_candidate_rows(batch_id, tier, low, high)` (rows with `image_id1`, `status`... see its select: columns `image_id1, filename, status, image_id2, filename, status, distance, ...`; the two status columns come back as `img1.status`, `img2.status`).
- Produces (all in `Backend.app.services.ingestion_metrics`; none commits; each swallows `Exception` inside a savepoint and logs `ingestion metrics failed (<label>): ...`):
  - `TIER_PROCESS = {"tier_a": "ingest_tier_a", "tier_b": "ingest_tier_b"}`
  - `async record_tier_proposals(session, tier: str, batch_id, low: float, high: float) -> None`
  - `async record_proposed(session, tier: str, batch_id, image_ids) -> None`
  - `async record_decision(session, tier: str, event: str, image_id, batch_id) -> None`
  - `async retract_rejection(session, image_id) -> None`
  - `async record_implicit_keeps(session, batch_id, promoted_ids) -> None`

- [ ] **Step 1: Write the failing tests** (`tests/integration/test_ingestion_metrics.py`; helpers copied from `tests/integration/test_ingestion_popularity.py`: `_make_run` via `BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_a_review")`, `_img(session, status, batch_id)`, `_pair(session, id1, id2, distance)` with `TmpDuplicates(image_id1=min, image_id2=max, distance=..., match_source="in_batch")`; plus a helper `_events(session, process, subject)` returning the set of event names for a subject by selecting `ProcessEvent.event`):

```python
async def test_record_tier_proposals_covers_only_pending_members_of_the_run_in_the_band(db_session):
    run, other_run = await _make_run(db_session), await _make_run(db_session)
    a = await _img(db_session, "pending", run)
    b = await _img(db_session, "pending", run)
    active = await _img(db_session, "active")
    stranger = await _img(db_session, "pending", other_run)   # another run's pending image
    await _pair(db_session, a, active, 0.02)                  # tier A band
    await _pair(db_session, b, stranger, 0.07)                # tier B band, partner of another run

    await record_tier_proposals(db_session, "tier_a", run, 0.0, 0.05)
    await record_tier_proposals(db_session, "tier_b", run, 0.05, 0.12)

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_a", b) == set()
    assert await _events(db_session, "ingest_tier_b", a) == set()          # 0.02 is not in band B
    assert await _events(db_session, "ingest_tier_b", b) == {"proposed"}   # 0.07 is band B only
    assert await _events(db_session, "ingest_tier_a", active) == set()
    assert await _events(db_session, "ingest_tier_b", stranger) == set()   # not this run's image


async def test_record_tier_proposals_is_idempotent_and_skips_reviewed_and_rejected_pairs(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)
    reviewed = await _img(db_session, "pending", run)
    rejected_partner = await _img(db_session, "pending", run)
    gone = await _img(db_session, "rejected", run)
    active = await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _pair(db_session, reviewed, active, 0.02, tier_a_reviewed_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    await _pair(db_session, rejected_partner, gone, 0.02)

    await record_tier_proposals(db_session, "tier_a", run, 0.0, 0.05)
    await record_tier_proposals(db_session, "tier_a", run, 0.0, 0.05)

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_a", reviewed) == set()
    assert await _events(db_session, "ingest_tier_a", rejected_partner) == set()
    counts = (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
    assert next(i for i in counts if i["run_id"] == str(run))["counts"]["proposed"] == 1


async def test_record_decision_needs_a_proposal_in_that_tier(db_session):
    run = await _make_run(db_session)
    proposed, never = await _img(db_session, "pending", run), await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [proposed])

    await record_decision(db_session, "tier_a", "rejected", proposed, run)
    await record_decision(db_session, "tier_a", "kept", never, run)
    await record_decision(db_session, "tier_b", "kept", proposed, run)   # proposed in A, not in B

    assert await _events(db_session, "ingest_tier_a", proposed) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", never) == set()
    assert await _events(db_session, "ingest_tier_b", proposed) == set()


async def test_a_conflicting_decision_is_swallowed_and_leaves_the_session_usable(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [a])
    await record_decision(db_session, "tier_a", "rejected", a, run)

    await record_decision(db_session, "tier_a", "kept", a, run)          # exclusive with rejected: swallowed

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "rejected"}
    assert (await db_session.execute(select(Image.id).where(Image.id == a))).scalar_one() == a   # session still usable


async def test_a_failing_metrics_write_is_swallowed(db_session, monkeypatch):
    async def boom(self, *args, **kwargs):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)

    await record_proposed(db_session, "tier_a", run, [a])                 # must not raise
    await record_decision(db_session, "tier_a", "rejected", a, run)

    assert await _events(db_session, "ingest_tier_a", a) == set()


async def test_retract_rejection_clears_either_tier(db_session):
    run = await _make_run(db_session)
    a, b = await _img(db_session, "pending", run), await _img(db_session, "pending", run)
    await record_proposed(db_session, "tier_a", run, [a])
    await record_proposed(db_session, "tier_b", run, [b])
    await record_decision(db_session, "tier_a", "rejected", a, run)
    await record_decision(db_session, "tier_b", "rejected", b, run)

    await retract_rejection(db_session, a)
    await retract_rejection(db_session, b)
    await retract_rejection(db_session, uuid.uuid4())                      # nothing to retract: no error

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_b", b) == {"proposed"}


async def test_record_implicit_keeps_covers_only_proposed_and_undecided_images(db_session):
    run = await _make_run(db_session)
    undecided, decided, never = (await _img(db_session, "pending", run) for _ in range(3))
    await record_proposed(db_session, "tier_a", run, [undecided, decided])
    await record_proposed(db_session, "tier_b", run, [undecided])
    await record_decision(db_session, "tier_a", "rejected", decided, run)

    await record_implicit_keeps(db_session, run, [undecided, decided, never])
    await record_implicit_keeps(db_session, run, [undecided, decided, never])      # idempotent

    assert await _events(db_session, "ingest_tier_a", undecided) == {"proposed", "kept"}
    assert await _events(db_session, "ingest_tier_b", undecided) == {"proposed", "kept"}
    assert await _events(db_session, "ingest_tier_a", decided) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", never) == set()
    await record_implicit_keeps(db_session, run, [])                                # empty list is fine
```
Imports: `uuid`, `datetime/timezone`, `pytest`, `select`, `Image`, `ProcessEvent`, `TmpDuplicates`, `BatchRunRepository`, `BusinessMetricsService`, and the five functions. `_pair` takes `**kw` for `tier_a_reviewed_at`.

- [ ] **Step 2: Run to verify failure** (module missing).
- [ ] **Step 3: Implement** `Backend/app/services/ingestion_metrics.py`:

```python
"""Business metrics of the ingestion duplicate review (epic screenshot-recut, M2a). The only place the ingestion code
touches process_events. Every function runs its writes in a savepoint and swallows any Exception with a warning: a
metrics problem must never fail or roll back a review decision, a candidate search or a promotion. Never commits."""
import logging
import uuid
from typing import Awaitable, Callable, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from Storage.models import Image

logger = logging.getLogger(__name__)
T = TypeVar("T")

TIER_PROCESS = {"tier_a": "ingest_tier_a", "tier_b": "ingest_tier_b"}
PROPOSED, REJECTED, KEPT = "proposed", "rejected", "kept"


async def _swallowed(session: AsyncSession, label: str, call: Callable[[], Awaitable[T]]) -> T | None:
    try:
        async with session.begin_nested():
            return await call()
    except Exception as exc:
        logger.warning("ingestion metrics failed (%s): %r", label, exc)
        return None


async def record_proposed(session: AsyncSession, tier: str, batch_id, image_ids) -> None:
    async def call():
        service = BusinessMetricsService(session)
        for image_id in image_ids:
            await service.record(TIER_PROCESS[tier], PROPOSED, batch_id, image_id)
    await _swallowed(session, f"proposed {tier}", call)


async def record_tier_proposals(session: AsyncSession, tier: str, batch_id, low: float, high: float) -> None:
    """`proposed` for every pending image of the run that the tier's review queue would show (the same rows as
    IngestionRepository.get_tier_candidate_rows: in the band, not yet reviewed in this tier, neither side rejected)."""
    async def call():
        rows = await IngestionRepository(session).get_tier_candidate_rows(batch_id, tier, low, high)
        pending = {row[0] for row in rows if row[2] == "pending"} | {row[3] for row in rows if row[5] == "pending"}
        if not pending:
            return
        ids = (await session.execute(select(Image.id).where(
            Image.id.in_(pending), Image.ingestion_batch_id == batch_id, Image.status == "pending"))).scalars().all()
        await record_proposed(session, tier, batch_id, ids)
    await _swallowed(session, f"tier proposals {tier}", call)


async def record_decision(session: AsyncSession, tier: str, event: str, image_id, batch_id) -> None:
    async def call():
        service = BusinessMetricsService(session)
        process = TIER_PROCESS[tier]
        if not await service.with_event(process, PROPOSED, [image_id]):
            return  # it never entered this tier's review: a stale or out-of-queue decision counts for nothing
        await service.record(process, event, batch_id, image_id)
    await _swallowed(session, f"{event} {tier}", call)


async def retract_rejection(session: AsyncSession, image_id) -> None:
    async def call():
        service = BusinessMetricsService(session)
        for process in TIER_PROCESS.values():
            await service.retract(process, REJECTED, image_id)
    await _swallowed(session, "retract rejection", call)


async def record_implicit_keeps(session: AsyncSession, batch_id, promoted_ids) -> None:
    """A promoted image that was proposed in a tier and never decided there was left in the library: count it as kept."""
    promoted = list(promoted_ids)
    if not promoted:
        return

    async def call():
        service = BusinessMetricsService(session)
        for process in TIER_PROCESS.values():
            for image_id in await service.undecided(process, PROPOSED, (REJECTED, KEPT), promoted):
                await service.record(process, KEPT, batch_id, image_id)
    await _swallowed(session, "implicit keeps", call)
```
(The tuple indexes follow `get_tier_candidate_rows`'s select: index 0 `image_id1`, 2 `img1.status`, 3 `image_id2`, 5 `img2.status`.) `uuid` may be unused: drop the import if so.
- [ ] **Step 4: Run to verify pass** (the new file; then `tests/integration/test_business_metrics_service.py`).
- [ ] **Step 5: Commit** (`feat(recut): M2a ingestion metrics module`).

---

### Task 3: Record decisions and undos in `IngestionService`

**Files:**
- Modify: `Backend/app/services/ingestion_service.py`
- Test: `tests/integration/test_ingestion_metrics_flow.py`

**Interfaces:**
- Consumes: `record_decision`, `retract_rejection` (Task 2).
- Produces: `resolve` records `rejected` / `kept`; `undo_reject` retracts.

- [ ] **Step 1: Write the failing tests** (`tests/integration/test_ingestion_metrics_flow.py`; helpers and constants copied from `tests/integration/test_ingestion_popularity.py`: `_make_run`, `_img`, `_pair`, `_service(session) = IngestionService(IngestionRepository(session))`, `MOVE_OUT = "Backend.app.services.ingestion_service.image_store.move_to_rejected"`, `MOVE_BACK = "...move_from_rejected"`; wrap calls in `with patch(MOVE_OUT):`):

```python
async def _proposed(session, tier, run, *image_ids):
    await record_proposed(session, tier, run, image_ids)


async def test_a_reject_records_rejected_and_a_keep_records_kept(db_session):
    run = await _make_run(db_session)
    a, b, active = await _img(db_session, "pending", run), await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _pair(db_session, b, active, 0.02)
    await _proposed(db_session, "tier_a", run, a, b)

    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [
            {"image_id": a, "decision": "reject"}, {"image_id": b, "decision": "keep"}])

    assert result["rejected"] == [str(a)] and result["kept"] == [str(b)]
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "rejected"}
    assert await _events(db_session, "ingest_tier_a", b) == {"proposed", "kept"}


async def test_a_decision_for_an_image_that_was_never_proposed_counts_nothing(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "pending", run)
    with patch(MOVE_OUT):
        await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "keep"}])
    assert await _events(db_session, "ingest_tier_a", a) == set()


async def test_a_stale_reject_of_a_non_pending_image_counts_nothing(db_session):
    run = await _make_run(db_session)
    a = await _img(db_session, "active", run)
    await _proposed(db_session, "tier_a", run, a)
    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    assert result["rejected"] == []
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}


async def test_a_metrics_failure_leaves_the_decision_applied(db_session, monkeypatch):
    run = await _make_run(db_session)
    a, b, active = await _img(db_session, "pending", run), await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _pair(db_session, b, active, 0.02)
    await _proposed(db_session, "tier_a", run, a, b)      # seed BEFORE the failure is installed

    async def boom(self, *args, **kwargs):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)

    with patch(MOVE_OUT):
        result = await _service(db_session).resolve("tier_a", [
            {"image_id": a, "decision": "reject"}, {"image_id": b, "decision": "keep"}])

    assert result["rejected"] == [str(a)] and result["kept"] == [str(b)] and result["failed"] == []
    assert (await db_session.get(Image, a)).status == "rejected"
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}
    assert await _events(db_session, "ingest_tier_a", b) == {"proposed"}


async def test_undo_reject_then_keep_counts_one_outcome(db_session):
    run = await _make_run(db_session)
    a, active = await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _proposed(db_session, "tier_a", run, a)
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    with patch(MOVE_BACK):
        await service.undo_reject(a)
    assert await _events(db_session, "ingest_tier_a", a) == {"proposed"}

    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "keep"}])

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "kept"}


async def test_undo_reject_then_reject_again_counts_one_rejection(db_session):
    run = await _make_run(db_session)
    a, active = await _img(db_session, "pending", run), await _img(db_session, "active")
    await _pair(db_session, a, active, 0.02)
    await _proposed(db_session, "tier_a", run, a)
    service = _service(db_session)
    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])
    with patch(MOVE_BACK):
        await service.undo_reject(a)

    with patch(MOVE_OUT):
        await service.resolve("tier_a", [{"image_id": a, "decision": "reject"}])

    assert await _events(db_session, "ingest_tier_a", a) == {"proposed", "rejected"}
    item = next(i for i in (await BusinessMetricsService(db_session).batch_metrics("ingest_tier_a"))["items"]
                if i["run_id"] == str(run))
    assert item["counts"] == {"proposed": 1, "rejected": 1, "kept": 0}
```
The helper `_events(session, process, subject)` is defined in this file (select `ProcessEvent.event` where process and subject_id) and may be imported from `tests/integration/test_ingestion_metrics.py` only if that file exposes it from a shared helper; copy it instead.
- [ ] **Step 2: Run to verify failure** (the recording tests fail: no events).
- [ ] **Step 3: Implement.** In `ingestion_service.py` import `from Backend.app.services.ingestion_metrics import record_decision, retract_rejection`. In `resolve`: after `await self._record_reject(image_id, tier, reject_ids, keep_ids)` add `await record_decision(self.repo.session, tier, "rejected", image_id, await self.repo.get_batch_id(image_id))`; in the `keep` branch after `await self.repo.mark_reviewed(image_id, tier)` add the same with `"kept"`. In `undo_reject`, after the `guarded(... reverse_open_edge ...)` call add `await retract_rejection(self.repo.session, image_id)`. (`self.repo.session` is the repository's session, as `_record_reject` already uses.)
- [ ] **Step 4: Run to verify pass,** then `tests/integration/test_ingestion_popularity.py`, `test_ingestion_partial_resolve.py`, `test_ingestion_resolve_atomicity.py`, `test_ingestion_tier_b_review.py`, `test_ingestion_review_progress.py`.
- [ ] **Step 5: Commit** (`feat(recut): M2a record review decisions and undos`).

---

### Task 4: Record proposals in the find job and implicit keeps in promote

**Files:**
- Modify: `batch/ingest_find_duplicates.py`, `batch/ingest_promote.py`
- Test: `tests/integration/test_ingest_find_duplicates.py` (add), `tests/integration/test_ingest_promote.py` (add)

**Interfaces:**
- Consumes: `record_tier_proposals`, `record_implicit_keeps` (Task 2); `_tier_band` from `Backend.app.services.ingestion_service`.
- Produces: `ingest_find_duplicates.record_proposals(session, batch_id, tier) -> None` (reads the tier band and calls `record_tier_proposals`); `promote(session, batch_id, tier_b_threshold)` records the implicit keeps (the call sits inside `promote` so it is testable; it runs at the same point the spec names: after the status flip, before the caller's commit).

- [ ] **Step 1: Write the failing tests.**
  - `test_ingest_find_duplicates.py` (uses unit vectors; copy `_insert_image`): a pending image `a` identical to an active image (distance 0) → after `find_batch_duplicates(..., threshold=0.3)` then `await record_proposals(db_session, batch_id, "tier_a")` and `"tier_b"`: `a` has `proposed` in `ingest_tier_a` and none in `ingest_tier_b` (distance 0 is band A); a second call to both adds nothing (`batch_metrics("ingest_tier_a")` proposed count for the run stays 1); a pending image of another run with an identical vector is not proposed under this run. For a band-B case insert `tmp_duplicates` directly (`distance=0.08`) and assert it is proposed in B only.
  - `test_ingest_promote.py`: image `p` pending in the run, proposed in both tiers, no candidate pair (so promotable) → `promote(...)` returns `[p]` and `p` has `kept` in both tiers; a second promoted image `d` proposed in tier A and already `rejected`-recorded... (use a `kept` pre-recorded one) gets no second outcome; an image never proposed gets nothing; after promoting everything, `batch_metrics` shows `pending == 0` for the run in tier A (proposed == rejected + kept).
  - Also a partner-rejection scenario: pending `x` and pending `y` in the run with a 0.02 pair, both proposed in tier A; `y` is rejected through `IngestionService.resolve` (patch `move_to_rejected`); `get_promotable_ids` then lets `x` through; `promote` records `x` as `kept` in tier A and the run's tier A `pending` is 0.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
  - `ingest_find_duplicates.py`:

```python
from Backend.app.services.ingestion_metrics import record_tier_proposals
from Backend.app.services.ingestion_service import _tier_band


async def record_proposals(session, batch_id, tier: str) -> None:
    """The pending images of the run that this tier's review will show are the denominators of its business metrics (M2a)."""
    low, high = _tier_band(tier)
    await record_tier_proposals(session, tier, batch_id, low, high)
```
  and in `main`, after `find_batch_duplicates(...)`: `await record_proposals(session, active_run.run_id, tier)` (before `set_stage` / the commit). Check for an import cycle by running the module's tests; if `ingestion_service` imports the batch module somewhere, import `_tier_band` lazily inside `record_proposals`.
  - `ingest_promote.py`: in `promote`, after `await repo.promote_images(promotable)` add `await record_implicit_keeps(session, batch_id, promotable)` (import from `Backend.app.services.ingestion_metrics`).
- [ ] **Step 4: Run to verify pass:** both files, then the whole `tests/integration/test_ingest_*.py` set, `batch/tests/`.
- [ ] **Step 5: Commit** (`feat(recut): M2a record proposals and implicit keeps`).

---

### Task 5: Panels on `/admin/batches`

**Files:**
- Modify: `Frontend/memes-frontend/src/components/BusinessMetricsPanel.tsx`, `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`
- Test: `Frontend/memes-frontend/src/components/BusinessMetricsPanel.test.tsx`, `Frontend/memes-frontend/src/pages/AdminBatchesPage.test.tsx`

**Interfaces:**
- Produces: `BusinessMetricsPanel` prop `note?: string` (default `"Pending proposals are not counted as rejections."`).

- [ ] **Step 1: Write the failing tests.** Panel: (a) with `note="Reject share, not precision."` the custom text shows and the default sentence does not; (b) without `note` the default shows (the existing first test already asserts it); (c) a response with `counts: { proposed: 4, rejected: 2, kept: 1 }` and `metrics: { reject_share: 0.6667, decided_share: 0.75, pending: 1 }` renders headers "Proposed", "Rejected", "Kept", "Reject share", "Decided share" and "66.7%". Page: with `getBatchMetrics` mocked, the page requests `recut`, `ingest_tier_a` and `ingest_tier_b` (`expect(api.getBatchMetrics).toHaveBeenCalledWith("ingest_tier_a")`, etc.) and shows the titles "Ingestion duplicates: Tier A" and "Ingestion duplicates: Tier B" (use the empty state texts when the mock returns no items: "No tier A duplicate candidates yet", "No tier B duplicate candidates yet").
- [ ] **Step 2: Run to verify failure** (`npx vitest run src/components/BusinessMetricsPanel.test.tsx src/pages/AdminBatchesPage.test.tsx`).
- [ ] **Step 3: Implement.** `Props` gains `note?: string`; `Body` takes it and renders `{note ?? "Pending proposals are not counted as rejections."}` in the existing `<p className="text-xs text-gray-500 mt-2">`. In `AdminBatchesPage.tsx` after the recut panel:

```tsx
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="ingest_tier_a"
        title="Ingestion duplicates: Tier A"
        emptyText="No tier A duplicate candidates yet"
        note={INGEST_NOTE}
      />
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="ingest_tier_b"
        title="Ingestion duplicates: Tier B"
        emptyText="No tier B duplicate candidates yet"
        note={INGEST_NOTE}
      />
```
with `const INGEST_NOTE = "Reject share, not precision: a kept image can be the winner of a true duplicate group. Pending images are not counted as kept."` at module level. Update `src/test/mockApi.ts` only if its default `getBatchMetrics` response breaks a test (it returns the recut-shaped empty response for any process, which is fine).
- [ ] **Step 4: Run to verify pass,** then `npx vitest run`, `npx tsc -b`, `npx eslint src/ --max-warnings 0`.
- [ ] **Step 5: Commit** (`feat(recut): M2a ingestion panels`).

---

### Task 6: Docs, status and full verification

**Files:**
- Modify: `docs/superpowers/specs/2026-10-17-screenshot-recut/adr/ADR-002-business-metrics.md`, `backend_api.md`, `docs/batches/use-cases/ingestion.md`, `docs/batches/inventory.md`, `CLAUDE.md`, the M2a spec (status), `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`
- Test: `tests/docs/`

- [ ] **Step 1: Write the docs.**
  - ADR-002: an "Amendment (M2a, 2026-10-30)" section: `process_events` stays append-only except `BusinessMetricsService.retract`, used only when a human undoes the decision an event describes (ingestion `undo_reject`); no other code removes events.
  - `backend_api.md` "Batch Metrics": `process` today `recut`, `ingest_tier_a`, `ingest_tier_b`; for the tier processes the events are `proposed`, `rejected`, `kept` and the metrics `reject_share` (rejected / (rejected + kept)), `decided_share`, `pending`; a kept image can be the winner of a true duplicate group, so it is a reject share, not a precision; a run's images that were aborted stay pending. Update the example only if it names the process list.
  - `docs/batches/use-cases/ingestion.md`: a "Review metrics" section: what is recorded where (find job → `proposed` per tier; review → `rejected`/`kept`; undo retracts; promote records implicit keeps), the two panels, the caveats (kept winner; aborted run; a metrics failure under-reports by one and is logged).
  - `CLAUDE.md` and `docs/batches/inventory.md`: one sentence each on the `ingest_find_duplicates` (records the tier's `proposed` events) and `ingest_promote` (records `kept` for undecided proposed images) entries. Find them with `grep -n "ingest_find_duplicates\|ingest_promote" CLAUDE.md docs/batches/inventory.md`.
  - M2a spec: `Status: done` and a `Plan:` line; tracker row M2a `done` with the plan link.
- [ ] **Step 2: Full verification, each root separately** (redirect long output to a scratch file and read its tail): `tests/integration/` (with the test DB URL), `cd Backend && pytest`, `batch/tests/`, `tests/evaluation/`, `tests/rules/`, `tests/docs/`, `tests/docker/` (skip allowed), `npx vitest run`, `npx tsc -b`, `npx eslint src/ --max-warnings 0`. No schema changed, so no type regeneration is needed; confirm with `git status` that no generated file moved.
- [ ] **Step 3: Commit** (`docs(recut): M2a done`).
