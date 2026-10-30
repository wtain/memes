# M2b Library Duplicate Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record `confirmed` and `dismissed` events for the owner's decisions on the library `/duplicates` page, attributed to the latest completed `clusterize` run, and show them as one panel on `/admin/batches`.

**Architecture:** One new process `library_duplicates` in the M1 registry. A new `library_metrics` module (savepoint-and-swallow, like M2a's `ingestion_metrics`) is called from four places in `ImageService`: `mark_flagged`, `unmark_flagged`, `dismiss_cluster`, `undo_dismiss`. Three small read helpers support attribution and the undo rules. The existing metrics endpoint and `BusinessMetricsPanel` serve the process.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL (`process_events`), React + vitest.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-30-m2b-library-duplicate-metrics-design.md`

## Global Constraints

- Process `library_duplicates`; events `confirmed`, `dismissed`; no exclusive group; metric `confirm_share` = confirmed / (confirmed + dismissed), `None` on a zero denominator.
- Subject = the image. `confirmed` = flagged loser(s) and their keeper; `dismissed` = every member of a dismissed set.
- Only `mark_flagged` with reason `duplicate_review` records; `agent_review`, `file_duplicates`, no reason: nothing.
- Batch = the latest `clusterize` run with status `completed` at decision time, else `None`.
- Every metrics write runs in a savepoint (`session.begin_nested()`), logs a warning and swallows any `Exception`; `BaseException` is not caught; none of it commits.
- `unmark_flagged` retracts the loser's `confirmed`, and the keeper's when no open `cluster_review` absorb edge points at it any more. `undo_dismiss` retracts an image's `dismissed` only when no `duplicate_decisions` pair involving it remains.
- No migration, no new endpoint, no new shared schema (the batch-metrics schema is a free map).
- Python 3.11 venv by absolute path: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe`. Test DB only: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`. Never a live `DATABASE_URL`; no live runs.
- Test roots run separately (`tests/integration/`, `Backend/` from its dir, `batch/tests/`, `tests/evaluation/`, `tests/rules/`, `tests/docs/`, vitest). The shared test DB may deadlock under load: rerun, do not "fix". Known unrelated failure: `Backend/tests/test_batch_subprocess.py::...::test_forces_utf8_output_encoding_in_child_env`.
- Worktree guard: plain single commands (no heredocs, no `git -C`, no commands whose arguments are computed from other command output); create and edit files with the Write/Edit tools. Many files are CRLF: use the Edit tool. Frontend `node_modules`: junction the main checkout's with PowerShell `New-Item -ItemType Junction` and remove the junction with `cmd /c rmdir` (never a recursive delete) when finished.
- Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Nothing is pushed.

## Review Focus

- A metrics write that raises during a flag, unflag, dismissal or undo leaves that action applied and its response unchanged (Task 3).
- A flag with reason `agent_review` or no reason, and a flag with an invalid `duplicateOf`, record nothing (Task 3).
- Unflagging one of several losers of one keeper keeps the keeper's `confirmed`; unflagging the last retracts it (Tasks 2 and 3).
- An unflag of an image that an agent flagged (`agent_review` edge) must not retract anything of the page's (Task 3).
- `undo_dismiss` of part of a cluster keeps `dismissed` for images that still have a decision pair (Tasks 2 and 3).
- A started or failed `clusterize` run is never the attribution target; no completed run means unattributed (Task 2).

---

### Task 1: Read helpers

**Files:**
- Modify: `repository/popularity.py`, `Backend/app/repositories/duplicate_decisions_repository.py`, `repository/batch_runs.py`
- Test: `tests/integration/test_library_metrics_helpers.py`

**Interfaces:**
- Produces:
  - `PopularityRepository.open_winner(loser_id: uuid.UUID, sources: Collection[str]) -> uuid.UUID | None`
  - `PopularityRepository.has_open_edge_to(winner_id: uuid.UUID, sources: Collection[str]) -> bool`
  - `DuplicateDecisionsRepository.images_with_decisions(image_ids: Collection[uuid.UUID]) -> set[uuid.UUID]`
  - `BatchRunRepository.get_latest_completed_run(kind: str) -> BatchRun | None`

- [ ] **Step 1: Write the failing tests** (`tests/integration/test_library_metrics_helpers.py`, `@pytest.mark.asyncio(loop_scope="session")`, `db_session`; reuse `make_image` from `tests.integration.test_popularity_repository`):

```python
async def test_open_winner_is_the_keeper_of_an_open_edge_of_a_listed_source(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(l.id, w.id, "cluster_review")

    assert await repo.open_winner(l.id, {"cluster_review"}) == w.id
    assert await repo.open_winner(l.id, {"agent_review"}) is None            # another source
    assert await repo.open_winner(w.id, {"cluster_review"}) is None          # no edge as a loser


async def test_open_winner_is_none_once_the_edge_is_reversed(db_session):
    w, l = await make_image(db_session, "w.jpg"), await make_image(db_session, "l.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(l.id, w.id, "cluster_review")
    await repo.reverse_open_edge(l.id, "unflag")

    assert await repo.open_winner(l.id, {"cluster_review"}) is None


async def test_has_open_edge_to_counts_only_open_edges_of_the_listed_sources(db_session):
    w = await make_image(db_session, "w.jpg")
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    repo = PopularityRepository(db_session)
    await repo.absorb(a.id, w.id, "cluster_review")
    await repo.absorb(b.id, w.id, "agent_review")

    assert await repo.has_open_edge_to(w.id, {"cluster_review"}) is True
    assert await repo.has_open_edge_to(w.id, {"file_duplicates"}) is False
    await repo.reverse_open_edge(a.id, "unflag")
    assert await repo.has_open_edge_to(w.id, {"cluster_review"}) is False    # reversed
    assert await repo.has_open_edge_to(w.id, {"cluster_review", "agent_review"}) is True
    assert await repo.has_open_edge_to(c.id, {"cluster_review"}) is False


async def test_images_with_decisions_finds_either_side_of_a_pair(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    lone = await make_image(db_session, "lone.jpg")
    repo = DuplicateDecisionsRepository(db_session)
    await repo.record_decisions_bulk([(a.id, b.id), (c.id, a.id)])

    assert await repo.images_with_decisions([a.id, b.id, c.id, lone.id]) == {a.id, b.id, c.id}
    assert await repo.images_with_decisions([lone.id]) == set()
    assert await repo.images_with_decisions([]) == set()
    await repo.delete_decisions([(a.id, b.id)])
    assert await repo.images_with_decisions([b.id]) == set()
    assert await repo.images_with_decisions([a.id]) == {a.id}


async def test_get_latest_completed_run_ignores_started_and_failed_runs_and_other_kinds(db_session):
    older = _run("clusterize", "completed", day=1)
    newer = _run("clusterize", "completed", day=2)
    started = _run("clusterize", "started", day=3)
    failed = _run("clusterize", "failed", day=4)
    other = _run("rebuild_duplicates", "completed", day=5)
    db_session.add_all([older, newer, started, failed, other])
    await db_session.flush()
    repo = BatchRunRepository(db_session)

    assert (await repo.get_latest_completed_run("clusterize")).run_id == newer.run_id
    assert (await repo.get_latest_completed_run("no_such_kind_xyz")) is None
```
with the helper

```python
def _run(kind, status, day):
    # a year no other test uses, so these rows are the newest of their kind in the shared test database
    return BatchRun(kind=kind, trigger="manual", status=status, created_at=datetime(2031, 1, day, tzinfo=timezone.utc))
```
and `status` given as `str(RunStatus.completed)`, `str(RunStatus.started)`, `str(RunStatus.failed)` (check the `RunStatus` members in `Storage/models.py`; use the failed member that exists). Only one `started` run per kind can exist: the test inserts one for `clusterize` and the shared DB has none (tests roll back).
- [ ] **Step 2: Run to verify failure** (`DATABASE_URL=... H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_library_metrics_helpers.py -q`): FAIL, missing methods.
- [ ] **Step 3: Implement.**
  - `repository/popularity.py` (in `PopularityRepository`, under "read-only checks"; add `Collection` to the typing import if missing):

```python
    async def open_winner(self, loser_id: uuid.UUID, sources: Collection[str]) -> uuid.UUID | None:
        """The winner of the loser's open absorb edge, only for an edge of one of `sources`."""
        edge = await self._open_edge(loser_id)
        return edge.winner_image_id if edge is not None and edge.source in sources else None

    async def has_open_edge_to(self, winner_id: uuid.UUID, sources: Collection[str]) -> bool:
        """Whether an open absorb edge of one of `sources` still points at `winner_id`."""
        row = (await self.session.execute(
            select(PopularityAbsorption.id).where(
                PopularityAbsorption.winner_image_id == winner_id,
                PopularityAbsorption.kind == PopularityKind.ABSORB,
                PopularityAbsorption.reversed_by_id.is_(None),
                PopularityAbsorption.source.in_(list(sources)),
            ).limit(1)
        )).first()
        return row is not None
```
  - `duplicate_decisions_repository.py`:

```python
    async def images_with_decisions(self, image_ids) -> set[uuid.UUID]:
        """Which of `image_ids` are on either side of some 'not duplicates' pair."""
        ids = list(image_ids)
        if not ids:
            return set()
        first = (await self.session.execute(
            select(DuplicateDecision.image_id1).where(DuplicateDecision.image_id1.in_(ids)))).scalars()
        second = (await self.session.execute(
            select(DuplicateDecision.image_id2).where(DuplicateDecision.image_id2.in_(ids)))).scalars()
        return set(first) | set(second)
```
  - `repository/batch_runs.py` (next to `get_most_recent_run`; `RunStatus` is already imported there):

```python
    async def get_latest_completed_run(self, kind: str) -> BatchRun | None:
        result = await self._session.execute(
            select(BatchRun)
            .where(BatchRun.kind == kind, BatchRun.status == str(RunStatus.completed))
            .order_by(BatchRun.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
```
- [ ] **Step 4: Run to verify pass,** then `tests/integration/test_popularity_repository.py` and `tests/integration/test_business_metrics_service.py`.
- [ ] **Step 5: Commit** (`feat(recut): M2b read helpers`).

---

### Task 2: Registry entry and the `library_metrics` module

**Files:**
- Modify: `Backend/app/services/business_metrics_service.py`
- Create: `Backend/app/services/library_metrics.py`
- Test: `tests/integration/test_business_metrics_service.py` (add), `tests/integration/test_library_metrics.py`

**Interfaces:**
- Consumes: Task 1 helpers; `BusinessMetricsService.record/retract`.
- Produces (in `Backend.app.services.library_metrics`; none commits; each swallows `Exception` inside a savepoint and logs `library metrics failed (<label>): ...`):
  - `PROCESS = "library_duplicates"`
  - `async record_confirmed(session, image_ids) -> None`
  - `async record_dismissed(session, image_ids) -> None`
  - `async open_keeper(session, loser_id) -> uuid.UUID | None` (the loser's open `cluster_review` winner, `None` on none or on failure)
  - `async retract_confirmed(session, loser_id, keeper_id) -> None`
  - `async retract_dismissed(session, image_ids) -> None`

- [ ] **Step 1: Write the failing tests.**
  - `test_business_metrics_service.py`: `test_the_library_formula` (`confirm_share({"confirmed": 3, "dismissed": 1}) == 0.75`, `None` for zeros); `test_confirmed_and_dismissed_can_coexist_on_one_subject` (record both for one subject, both return `True`, no `ConflictingEventError`).
  - `tests/integration/test_library_metrics.py` (helpers: `_events(session, subject)` selecting `ProcessEvent.event` for process `library_duplicates` and that subject; `_run_of(session, subject, event)` selecting `ProcessEvent.batch_run_id`; `_clusterize_run(session, status, year, day)` creating a `BatchRun(kind="clusterize", trigger="manual", status=..., created_at=datetime(year, 1, day, tzinfo=timezone.utc))`, use year 2032 so these are the newest in the shared DB; images via `make_image`):

```python
async def test_confirmed_and_dismissed_are_recorded_and_idempotent(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    await record_confirmed(db_session, [a.id, b.id, a.id])          # a duplicate id in the list is fine
    await record_confirmed(db_session, [a.id])
    await record_dismissed(db_session, [a.id])

    assert await _events(db_session, a.id) == {"confirmed", "dismissed"}
    assert await _events(db_session, b.id) == {"confirmed"}


async def test_events_go_to_the_newest_completed_clusterize_run(db_session):
    await _clusterize_run(db_session, "completed", 2032, 1)
    newest = await _clusterize_run(db_session, "completed", 2032, 2)
    await _clusterize_run(db_session, "started", 2032, 3)           # still running: not the clusters on screen
    await _clusterize_run(db_session, "failed", 2032, 4)
    a = await make_image(db_session, "a.jpg")
    await record_confirmed(db_session, [a.id])
    assert await _run_of(db_session, a.id, "confirmed") == newest.run_id


async def test_events_are_unattributed_without_a_completed_clusterize_run(db_session, monkeypatch):
    monkeypatch.setattr(BatchRunRepository, "get_latest_completed_run", AsyncMock(return_value=None))
    a = await make_image(db_session, "a.jpg")

    await record_dismissed(db_session, [a.id])

    assert await _events(db_session, a.id) == {"dismissed"}
    assert await _run_of(db_session, a.id, "dismissed") is None


async def test_retract_confirmed_keeps_the_keeper_while_another_open_flag_points_at_it(db_session):
    keeper = await make_image(db_session, "keeper.jpg")
    l1 = await make_image(db_session, "l1.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(l1.id, keeper.id, "cluster_review")
    await pop.absorb(l2.id, keeper.id, "cluster_review")
    await record_confirmed(db_session, [l1.id, l2.id, keeper.id])

    await pop.reverse_open_edge(l1.id, "unflag")
    await retract_confirmed(db_session, l1.id, keeper.id)
    assert await _events(db_session, l1.id) == set()
    assert await _events(db_session, keeper.id) == {"confirmed"}      # l2 still points at it

    await pop.reverse_open_edge(l2.id, "unflag")
    await retract_confirmed(db_session, l2.id, keeper.id)
    assert await _events(db_session, l2.id) == set()
    assert await _events(db_session, keeper.id) == set()


async def test_retract_confirmed_without_a_keeper_only_touches_the_loser(db_session):
    keeper = await make_image(db_session, "keeper.jpg")
    loser = await make_image(db_session, "loser.jpg")
    await record_confirmed(db_session, [loser.id, keeper.id])

    await retract_confirmed(db_session, loser.id, None)

    assert await _events(db_session, loser.id) == set()
    assert await _events(db_session, keeper.id) == {"confirmed"}


async def test_open_keeper_returns_the_cluster_review_winner_only(db_session):
    w = await make_image(db_session, "w.jpg")
    page = await make_image(db_session, "page.jpg")
    agent = await make_image(db_session, "agent.jpg")
    free = await make_image(db_session, "free.jpg")
    pop = PopularityRepository(db_session)
    await pop.absorb(page.id, w.id, "cluster_review")
    await pop.absorb(agent.id, w.id, "agent_review")

    assert await open_keeper(db_session, page.id) == w.id
    assert await open_keeper(db_session, agent.id) is None        # an agent flag is not the page's
    assert await open_keeper(db_session, free.id) is None         # no edge at all


async def test_retract_dismissed_skips_images_that_still_have_a_decision_pair(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    await DuplicateDecisionsRepository(db_session).record_decisions_bulk([(a.id, b.id), (b.id, c.id)])
    await record_dismissed(db_session, [a.id, b.id, c.id])
    await DuplicateDecisionsRepository(db_session).delete_decisions([(a.id, b.id)])

    await retract_dismissed(db_session, [a.id, b.id])

    assert await _events(db_session, a.id) == set()                   # no pair left
    assert await _events(db_session, b.id) == {"dismissed"}           # still in (b, c)
    assert await _events(db_session, c.id) == {"dismissed"}


async def test_a_failing_write_is_swallowed_and_the_session_stays_usable(db_session, monkeypatch):
    async def boom(self, *a, **k):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)
    a = await make_image(db_session, "a.jpg")
    await record_confirmed(db_session, [a.id])                         # must not raise
    await record_dismissed(db_session, [a.id])
    assert await _events(db_session, a.id) == set()
    assert (await db_session.execute(select(Image.id).where(Image.id == a.id))).scalar_one() == a.id
```
  Imports for this file: `AsyncMock`, `pytest`, `select`, `datetime`/`timezone`, `Image`, `ProcessEvent`, `BatchRun`, `BatchRunRepository`,
  `PopularityRepository`, `DuplicateDecisionsRepository`, `BusinessMetricsService`, `make_image`, and the five `library_metrics` functions
  (`record_confirmed`, `record_dismissed`, `open_keeper`, `retract_confirmed`, `retract_dismissed`).
- [ ] **Step 2: Run to verify failure** (module and process missing).
- [ ] **Step 3: Implement.**
  - `business_metrics_service.py`: add to `PROCESSES`:

```python
    "library_duplicates": ProcessDefinition(
        name="library_duplicates",
        # The owner's decisions on the library /duplicates page (M2b). Images in a confirmed group (flagged copies and
        # their keeper) are `confirmed`, members of a dismissed set `dismissed`; an image may be both over time.
        events=("confirmed", "dismissed"),
        metrics={"confirm_share": lambda c: _ratio(c["confirmed"], c["confirmed"] + c["dismissed"])},
    ),
```
  - `library_metrics.py`:

```python
"""Business metrics of the library duplicate review (epic screenshot-recut, M2b). The only place the /duplicates flows touch
process_events. Every function runs its writes in a savepoint and swallows any Exception with a warning: a metrics problem
must never fail or roll back a flag, an unflag, a dismissal or an undo. Never commits."""
import logging
import uuid
from typing import Awaitable, Callable, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService
from repository.batch_runs import BatchRunRepository
from repository.popularity import PopularityRepository

logger = logging.getLogger(__name__)
T = TypeVar("T")

PROCESS = "library_duplicates"
CONFIRMED, DISMISSED = "confirmed", "dismissed"
_FLAG_SOURCES = {"cluster_review"}  # the popularity source of a page flag (FLAG_REASON_SOURCES["duplicate_review"])


async def _swallowed(session: AsyncSession, label: str, call: Callable[[], Awaitable[T]]) -> T | None:
    try:
        async with session.begin_nested():
            return await call()
    except Exception as exc:
        logger.warning("library metrics failed (%s): %r", label, exc)
        return None


async def _record_all(session: AsyncSession, event: str, image_ids) -> None:
    # The clusters on screen come from the newest completed clusterize run; none yet means unattributed.
    run = await BatchRunRepository(session).get_latest_completed_run("clusterize")
    run_id = run.run_id if run is not None else None
    service = BusinessMetricsService(session)
    for image_id in dict.fromkeys(image_ids):
        await service.record(PROCESS, event, run_id, image_id)


async def record_confirmed(session: AsyncSession, image_ids) -> None:
    await _swallowed(session, "confirmed", lambda: _record_all(session, CONFIRMED, list(image_ids)))


async def record_dismissed(session: AsyncSession, image_ids) -> None:
    await _swallowed(session, "dismissed", lambda: _record_all(session, DISMISSED, list(image_ids)))


async def open_keeper(session: AsyncSession, loser_id) -> uuid.UUID | None:
    return await _swallowed(session, "open keeper", lambda: PopularityRepository(session).open_winner(loser_id, _FLAG_SOURCES))


async def retract_confirmed(session: AsyncSession, loser_id, keeper_id) -> None:
    async def call():
        service = BusinessMetricsService(session)
        await service.retract(PROCESS, CONFIRMED, loser_id)
        if keeper_id is not None and not await PopularityRepository(session).has_open_edge_to(keeper_id, _FLAG_SOURCES):
            await service.retract(PROCESS, CONFIRMED, keeper_id)
    await _swallowed(session, "retract confirmed", call)


async def retract_dismissed(session: AsyncSession, image_ids) -> None:
    async def call():
        ids = list(dict.fromkeys(image_ids))
        still = await DuplicateDecisionsRepository(session).images_with_decisions(ids)
        service = BusinessMetricsService(session)
        for image_id in ids:
            if image_id not in still:
                await service.retract(PROCESS, DISMISSED, image_id)
    await _swallowed(session, "retract dismissed", call)
```
- [ ] **Step 4: Run to verify pass:** the new test file, `test_business_metrics_service.py`, `test_ingestion_metrics.py`; also `cd Backend && pytest tests/test_admin_metrics_endpoint.py`.
- [ ] **Step 5: Commit** (`feat(recut): M2b library metrics module`).

---

### Task 3: Record the page's decisions in `ImageService`

**Files:**
- Modify: `Backend/app/services/image_service.py`
- Test: `tests/integration/test_library_metrics_flow.py`

**Interfaces:**
- Consumes: Task 2 functions; `ImageService(repo, decision_repo, ocr_lemmas_repo)`.
- Produces: the four hooks described in the spec's section 3.

- [ ] **Step 1: Write the failing tests** (`tests/integration/test_library_metrics_flow.py`; service built as `ImageService(ImageRepository(session), DuplicateDecisionsRepository(session), MagicMock())`; images via `make_image`; `_events(session, subject)` as in Task 2; cluster membership seeded with `TmpImageClusters(cluster_id=<int>, image_id=<id>)` rows (status active images, the default of `make_image`), cluster ids from `random.randint(10**8, 10**9)` to avoid collisions with real rows):

```python
def _service(session):
    return ImageService(ImageRepository(session), DuplicateDecisionsRepository(session), MagicMock())


async def test_a_page_flag_confirms_the_loser_and_the_keeper(db_session):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    await _service(db_session).mark_flagged(str(l.id), "duplicate_review", str(w.id))
    assert await _events(db_session, l.id) == {"confirmed"}
    assert await _events(db_session, w.id) == {"confirmed"}


async def test_a_flag_without_a_keeper_confirms_only_the_loser(db_session):
    l = await make_image(db_session, "l.jpg")
    await _service(db_session).mark_flagged(str(l.id), "duplicate_review")
    assert await _events(db_session, l.id) == {"confirmed"}


@pytest.mark.parametrize("reason", ["agent_review", None, "other"])
async def test_other_flags_record_nothing(db_session, reason):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)

    await service.mark_flagged(str(l.id), reason)
    if reason == "agent_review":
        await service.mark_flagged(str(l.id), reason, str(w.id))      # an agent flag with a keeper

    assert await _events(db_session, l.id) == set()
    assert await _events(db_session, w.id) == set()


async def test_a_flag_with_a_bad_duplicate_of_raises_before_anything_is_recorded(db_session):
    l = await make_image(db_session, "l.jpg")
    for bad in ("not-a-uuid", str(l.id)):
        with pytest.raises(HTTPException) as exc:
            await _service(db_session).mark_flagged(str(l.id), "duplicate_review", bad)
        assert exc.value.status_code == 422
    assert await _events(db_session, l.id) == set()


async def test_unflag_retracts_the_loser_and_the_keeper_once_nothing_points_at_it(db_session):
    w = await make_image(db_session, "w.jpg")
    l1 = await make_image(db_session, "l1.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l1.id), "duplicate_review", str(w.id))
    await service.mark_flagged(str(l2.id), "duplicate_review", str(w.id))

    await service.unmark_flagged(str(l1.id))
    assert await _events(db_session, l1.id) == set()
    assert await _events(db_session, w.id) == {"confirmed"}          # l2's flag still points at it
    await service.unmark_flagged(str(l2.id))
    assert await _events(db_session, l2.id) == set()
    assert await _events(db_session, w.id) == set()


async def test_flag_unflag_flag_again_confirms_again(db_session):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    await service.unmark_flagged(str(l.id))
    assert await _events(db_session, l.id) == set()

    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))

    assert await _events(db_session, l.id) == {"confirmed"}
    assert await _events(db_session, w.id) == {"confirmed"}


async def test_unflag_of_an_agent_flagged_image_touches_nothing(db_session):
    w2 = await make_image(db_session, "w2.jpg")
    l2 = await make_image(db_session, "l2.jpg")
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    service = _service(db_session)
    await service.mark_flagged(str(l2.id), "duplicate_review", str(w2.id))    # the page's own flag
    await service.mark_flagged(str(l.id), "agent_review", str(w.id))          # an agent flag

    await service.unmark_flagged(str(l.id))

    assert await _events(db_session, l2.id) == {"confirmed"}
    assert await _events(db_session, w2.id) == {"confirmed"}
    assert await _events(db_session, l.id) == set()
    assert await _events(db_session, w.id) == set()


async def _cluster(session, *images):
    cluster_id = random.randint(10**8, 10**9)
    for image in images:
        session.add(TmpImageClusters(cluster_id=cluster_id, image_id=image.id))
    await session.flush()
    return cluster_id


async def test_a_dismissal_records_every_member_and_an_undo_retracts_only_the_images_without_a_pair_left(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    cid = await _cluster(db_session, a, b, c)
    service = _service(db_session)
    pairs = await service.dismiss_cluster(cid)
    for image in (a, b, c):
        assert await _events(db_session, image.id) == {"dismissed"}

    await service.undo_dismiss([pairs[0]])                           # one pair of the three goes
    for image in (a, b, c):
        assert await _events(db_session, image.id) == {"dismissed"}  # each is still in another pair

    await service.undo_dismiss(pairs[1:])
    for image in (a, b, c):
        assert await _events(db_session, image.id) == set()


async def test_dismissing_a_subset_records_only_that_subset(db_session):
    a = await make_image(db_session, "a.jpg")
    b = await make_image(db_session, "b.jpg")
    c = await make_image(db_session, "c.jpg")
    cid = await _cluster(db_session, a, b, c)

    await _service(db_session).dismiss_cluster(cid, [a.id, b.id])

    assert await _events(db_session, a.id) == {"dismissed"}
    assert await _events(db_session, b.id) == {"dismissed"}
    assert await _events(db_session, c.id) == set()


async def test_a_flag_then_a_dismissal_of_one_image_leaves_both_events(db_session):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    other = await make_image(db_session, "other.jpg")
    cid = await _cluster(db_session, l, other)
    service = _service(db_session)
    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))

    await service.dismiss_cluster(cid)

    assert await _events(db_session, l.id) == {"confirmed", "dismissed"}


async def test_the_events_are_attributed_to_the_newest_completed_clusterize_run(db_session):
    run = BatchRun(kind="clusterize", trigger="manual", status=str(RunStatus.completed),
                   created_at=datetime(2032, 6, 1, tzinfo=timezone.utc))
    db_session.add(run)
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    await db_session.flush()

    await _service(db_session).mark_flagged(str(l.id), "duplicate_review", str(w.id))

    rows = (await db_session.execute(select(ProcessEvent.batch_run_id).where(
        ProcessEvent.process == "library_duplicates", ProcessEvent.subject_id == l.id))).scalars().all()
    assert rows == [run.run_id]


async def test_a_metrics_failure_leaves_the_flag_and_the_dismissal_applied(db_session, monkeypatch):
    w = await make_image(db_session, "w.jpg")
    l = await make_image(db_session, "l.jpg")
    other = await make_image(db_session, "other.jpg")
    cid = await _cluster(db_session, l, other)

    async def boom(self, *args, **kwargs):
        raise RuntimeError("metrics down")
    monkeypatch.setattr(BusinessMetricsService, "record", boom)
    service = _service(db_session)

    await service.mark_flagged(str(l.id), "duplicate_review", str(w.id))
    pairs = await service.dismiss_cluster(cid)

    flagged = (await db_session.execute(select(ImageExtras.flagged).where(ImageExtras.image_id == l.id))).scalar_one()
    assert flagged is True
    assert len(pairs) == 1 and await DuplicateDecisionsRepository(db_session).images_with_decisions([l.id]) == {l.id}
    assert await _events(db_session, l.id) == set()
```
  Imports for this file: `random`, `datetime`/`timezone`, `MagicMock`, `pytest`, `HTTPException`, `select`, `ImageRepository`,
  `DuplicateDecisionsRepository`, `ImageService`, `BusinessMetricsService`, `make_image` (from `tests.integration.test_popularity_repository`),
  and from `Storage.models`: `BatchRun`, `ImageExtras`, `ProcessEvent`, `RunStatus`, `TmpImageClusters`. The helper
  `_events(session, subject)` selects `ProcessEvent.event` for process `library_duplicates` and that subject.
- [ ] **Step 2: Run to verify failure** (events missing).
- [ ] **Step 3: Implement** in `image_service.py` (import `from Backend.app.services.library_metrics import open_keeper, record_confirmed, record_dismissed, retract_confirmed, retract_dismissed`):
  - `mark_flagged`, after the existing `if winner_id is not None: await guarded(...)` block:

```python
        if reason == "duplicate_review":
            confirmed = []
            try:
                confirmed.append(uuid.UUID(str(image_id)))
            except ValueError:
                pass
            if winner_id is not None:
                confirmed.append(winner_id)
            await record_confirmed(self.repo.session, confirmed)
```
  - `unmark_flagged`:

```python
    async def unmark_flagged(self, image_id):
        await self.repo.set_flagged(image_id, False)
        try:
            loser_id = uuid.UUID(str(image_id))
        except ValueError:
            return
        keeper_id = await open_keeper(self.repo.session, loser_id)   # before the edge is reversed
        await guarded(
            self.repo.session, "unflag",
            lambda: PopularityRepository(self.repo.session).reverse_open_edge(
                loser_id, "unflag", {"cluster_review", "agent_review", "file_duplicates"}))
        await retract_confirmed(self.repo.session, loser_id, keeper_id)
```
  - `dismiss_cluster`: after `await self.decision_repo.record_decisions_bulk(pairs)` add `await record_dismissed(self.repo.session, target_ids)`.
  - `undo_dismiss`: after `await self.decision_repo.delete_decisions(pairs)` add `await retract_dismissed(self.repo.session, {image_id for pair in pairs for image_id in pair})`.
- [ ] **Step 4: Run to verify pass,** then the neighbours: `tests/integration/test_popularity_flagging.py`, `test_popularity_endpoint.py`, `test_popularity_set_endpoint.py`, `test_ingestion_metrics_flow.py`, and `cd Backend && pytest -k "duplicate or flag or image_service"`.
- [ ] **Step 5: Commit** (`feat(recut): M2b record the page's duplicate decisions`).

---

### Task 4: Panel on `/admin/batches`

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`
- Test: `Frontend/memes-frontend/src/pages/AdminBatchesPage.test.tsx`

**Interfaces:**
- Consumes: `BusinessMetricsPanel` props (`note` exists since M2a).

- [ ] **Step 1: Write the failing tests** in `AdminBatchesPage.test.tsx`: (a) the page shows the title "Library duplicates" and the empty text "No library duplicate decisions yet" and calls `getBatchMetrics("library_duplicates")`; (b) with a library response `{ counts: { confirmed: 3, dismissed: 1 }, metrics: { confirm_share: 0.75 } }` it renders headers "Confirmed", "Dismissed", "Confirm share" and "75.0%" and the note text "Counts images in decided groups, not detector precision". The existing test `explains in the tier panels that the number is a reject share` mocks every non-recut process with the tier response and expects `getAllByText('66.7%')` to have length 2: adapt its mock so `library_duplicates` returns the recut-shaped empty response (or its own bucket) and the expectations stay at two tier panels.
- [ ] **Step 2: Run to verify failure** (`npx vitest run src/pages/AdminBatchesPage.test.tsx`).
- [ ] **Step 3: Implement.** Constant `const LIBRARY_NOTE = "Counts images in decided groups, not detector precision: a confirmed group is its flagged copies plus the keeper. Each image counts once per outcome, under the clusterize run that was current at its first decision."` and, after the tier B panel:

```tsx
      <BusinessMetricsPanel
        memesApi={memesApi}
        process="library_duplicates"
        title="Library duplicates"
        emptyText="No library duplicate decisions yet"
        note={LIBRARY_NOTE}
      />
```
- [ ] **Step 4: Run to verify pass,** then `npx vitest run`, `npx tsc -b`, `npx eslint src/ --max-warnings 0`.
- [ ] **Step 5: Commit** (`feat(recut): M2b library panel`).

---

### Task 5: Docs, status and full verification

**Files:**
- Modify: `backend_api.md`, `docs/batches/use-cases/duplicates-and-clusters.md`, `CLAUDE.md`, `docs/batches/inventory.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/adr/ADR-002-business-metrics.md`, the M2b spec (status), `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`
- Test: `tests/docs/`

- [ ] **Step 1: Write the docs.**
  - `backend_api.md` "Batch Metrics": `process` values add `library_duplicates`; a paragraph: events `confirmed` and `dismissed`, one per image, attributed to the latest completed `clusterize` run (unattributed when none); `confirm_share` = confirmed / (confirmed + dismissed); `confirmed` = flagged copies plus their keeper (page flags with reason `duplicate_review` only), `dismissed` = members of a dismissed set; undoing a flag or a dismissal retracts; an image counts once per event; not a precision.
  - `docs/batches/use-cases/duplicates-and-clusters.md`: a "Review metrics" section (what counts and what does not: agent and exact-copy flags; the symmetric group rule; attribution; limits: once per image, keeper stays counted if its popularity edge failed, runs that never completed receive nothing; the panel).
  - `CLAUDE.md` and `docs/batches/inventory.md`: one sentence on the `clusterize` entries that the library review's business metrics are attributed to its latest completed run (find with `grep -n "clusterize" CLAUDE.md docs/batches/inventory.md`).
  - ADR-002: add to the M2a amendment (or a short "Amendment (M2b)"): M2b is the second user of `retract`; ingestion and library metrics writes are swallowed on failure, so counts can be one short or disagree with states by one.
  - M2b spec `Status: done` and a `Plan:` line; tracker row M2b `done` with the plan link.
- [ ] **Step 2: Full verification, each root separately** (redirect long output to a scratch file and read its tail): `tests/integration/` (test DB URL), `cd Backend && pytest`, `batch/tests/`, `tests/evaluation/`, `tests/rules/`, `tests/docs/`, `npx vitest run`, `npx tsc -b`, `npx eslint src/ --max-warnings 0`. No schema changed: confirm with `git status` that no generated file moved.
- [ ] **Step 3: Commit** (`docs(recut): M2b done`).
