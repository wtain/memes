# R5a Approve and Reject Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two endpoints that approve or reject a `proposed` recut cut (state transition, flagging through the existing removal path, popularity edge, rating move, metric event), `unregister_deleted_images` support for flagged `cut_proposed` images, and the enabled Approve/Reject buttons with auto-advance on `/admin/recut`.

**Architecture:** A new `RecutDecisionService` runs one decision in the request's transaction: it locks the `image_cuts` row, checks the preconditions, applies the transition and records the event. `RecutRepository` gains the write helpers. Two POST routes in `Backend/app/api/recut.py` map the service's domain errors to 404/409. The frontend adds two API methods, a `decide` action in `useRecutQueue`, and the buttons and `A`/`R` keys in `RecutReviewPage`. No file is moved by a decision; `move_flagged` then `unregister_deleted_images` do that.

**Tech Stack:** FastAPI, SQLAlchemy async, pytest (integration against `ocrdb_test`, router tests with a mocked service), shared JSON schemas with generated TypeScript/Kotlin/Python types, React + Vite + vitest + eslint (React Compiler rules).

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r5a-approve-reject-lifecycle-design.md`

## Global Constraints

- No migration and no change to `Storage/models.py` (`state`, `decided_at` exist since R1).
- Decision endpoints: `POST /api/recut/proposals/{id}/approve` and `/reject`, no body, `200 {id, state, decidedAt}`; non-UUID id 422; unknown id 404 `{"detail": "Proposal not found"}`; every other refusal is a 409 whose `detail` is exactly one of `already_decided`, `cut_missing`, `source_missing`, `source_not_active`, `source_flagged`.
- Preconditions: `already_decided` (state is not `proposed`, both), `cut_missing` (cut id null, row gone, or status not `cut_proposed`, both), `source_missing` (approve), `source_not_active` (approve), `source_flagged` (approve). Checked in that order.
- Approve: cut image `cut_proposed` to `active`; source flagged via `ImageExtrasRepository.set_flagged(source_id, True, "recut_approved")`; `PopularityRepository.absorb(source_id, cut_id, PopularitySource.RECUT, detect_run_id)` through `repository.popularity.guarded`; `meme_text_ratings` of the source move to the cut; `state = approved`, `decided_at` set; `BusinessMetricsService.record("recut", "approved", detect_run_id, image_cuts.id)`. Reject: cut flagged with `"recut_rejected"`, `state = rejected`, `decided_at`, event `rejected`; source untouched.
- A decision moves no file and never commits (`get_async_db` commits; an exception rolls back every step).
- `unregister_deleted_images`: second pass over `cut_proposed` images that are flagged, same missing-file and is-directory checks as `active`; an unflagged `cut_proposed` image is never unregistered.
- Frontend: types come from the generated `all.d.ts`; none hand-written. `eslint src/` 0 warnings; StrictMode and the React Compiler rules (effects need a cancel guard, refs touched only in handlers and effects). No confirmation step, no undo, no bulk decide.
- Test roots run separately: `Backend/` from its own directory; `tests/integration/` with `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`; `batch/tests/`, `tests/rules/`, `tests/docs/` each on their own. Python is `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe` (the venv lives in the main checkout, not the worktree).
- Many repo files use CRLF; preserve line endings (use the Edit tool for existing files). Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never run a migration or a batch against a live metal/general/IT database.

## Review Focus

- Deciding the same proposal twice (double click, second tab) applies once and answers `already_decided`; pinned in Tasks 2 and 6.
- A failure halfway through a decision leaves nothing half applied (cut active but source not flagged): the service raises after mutating, and the request transaction rolls back. Pinned in Task 2 by the `cut_missing`/`source_*` orders (checks all run before any write) and in Task 3 by an endpoint test that the route re-raises as 409.
- A popularity problem must not block an approval; pinned in Task 2.
- A rejected cut with its file gone is unregistered while its proposal row survives as `rejected`; an undecided cut with a missing file (unplugged drive) is kept. Pinned in Task 4.
- The decision lock: the repository's lock statement uses `FOR UPDATE`. The integration fixture runs inside one savepointed connection, so a true two-connection race cannot be staged; Task 2 pins the lock by compiling the statement, and the sequential second decision pins the outcome. (Deviation from the spec's "race test", ledger it as a ruling.)

---

### Task 1: Decision schema and generated types

**Files:**
- Create: `shared/schemas/recutdecision.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`, `Backend/app/types/generated/` (new `recutdecision.py`, updated `__init__.py`)

**Interfaces:**
- Produces: TS type `RecutDecision { id: string; state: string; decidedAt: string }`; Python `Backend.app.types.generated.recutdecision.Schema`.

- [ ] **Step 1: Write the schema**

`shared/schemas/recutdecision.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "recutdecision.schema.json",
  "title": "RecutDecision",
  "description": "The outcome of approving or rejecting one recut proposal",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "id": { "type": "string" },
    "state": { "type": "string" },
    "decidedAt": { "type": "string" }
  },
  "required": ["id", "state", "decidedAt"]
}
```

In `all.schema.json`, after the `RecutProposalDetail` line add `"RecutDecision": { "$ref": "recutdecision.schema.json" },` (mind the commas; keep the file's line endings).

- [ ] **Step 2: Regenerate the three trees**

TypeScript, from `Frontend/`: `bash generate-types.sh` (run `pnpm install` in `Frontend/memes-frontend/` first if the worktree has no `node_modules`; if `npx` cannot reach `json-schema-to-typescript`, stop and ask, hand-add nothing).
Kotlin, from the repo root: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe AndroidClient/scripts/generate_dtos.py`.
Python, from `Backend/`:
`H:\workspace_sandbox\memes\.venv311\Scripts\datamodel-codegen.exe --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel`.
The generator rewrites a `# timestamp:` header in every module. Run `git status --short Backend/app/types/generated`, then `git checkout -- <file>` for each modified file whose only change is the timestamp line (keep `__init__.py` and the new `recutdecision.py`).

- [ ] **Step 3: Verify**

Run: `git diff --stat` and `git status --short`.
Expected: changes only in the schema pair, `all.d.ts` (+`RecutDecision`), `Models.kt` (+`RecutDecision`), `Backend/app/types/generated/__init__.py`, and the new `recutdecision.py`. Re-running `bash generate-types.sh` produces no further diff.

- [ ] **Step 4: Commit**

```bash
git add shared/schemas Frontend/memes-frontend/src/types/generated AndroidClient Backend/app/types/generated
git commit -m "feat(recut): R5a decision schema and generated types

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Repository write helpers and RecutDecisionService

**Files:**
- Modify: `Backend/app/repositories/recut_repository.py`
- Create: `Backend/app/services/recut_decision_service.py`
- Test: `tests/integration/test_recut_decision_service.py`

**Interfaces:**
- Consumes: `ImageExtrasRepository.set_flagged(image_id, flagged, remarks)` and `.get_flags_bulk(ids) -> dict` (`repository/image_extras.py`); `PopularityRepository.absorb(loser, winner, source, batch_run_id)` and `guarded(session, label, call)` (`repository/popularity.py`); `BusinessMetricsService(session).record(process, event, batch_run_id, subject_id) -> bool` raising `ConflictingEventError`; `Storage.models` `CutState`, `Image`, `ImageCut`, `ImageStatus`, `MemeTextRating`, `PopularitySource`.
- Produces (repository, all take/return plain values, never commit): `lock_proposal(cut_id: uuid.UUID) -> ImageCut | None` (`SELECT ... FOR UPDATE`), `images_by_id(ids: list[uuid.UUID]) -> dict[uuid.UUID, Image]`, `set_image_status(image_id, status: str) -> None` (Core `UPDATE`), `move_ratings(from_id, to_id) -> int`, `mark_decided(row: ImageCut, state: str) -> datetime`.
- Produces (service): `class ProposalNotFoundError(Exception)`; `class ProposalConflictError(Exception)` with `.code: str`; `RecutDecisionService(session)` with `async approve(cut_id: uuid.UUID) -> dict` and `async reject(cut_id: uuid.UUID) -> dict`, both returning `{"id": str, "state": str, "decidedAt": str}`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_recut_decision_service.py` (module docstring in the style of `test_recut_repository.py`; the test database is shared, so assert on seeded ids):

```python
"""RecutDecisionService: approve and reject a recut proposal (screenshot recut R5a). Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.services.recut_decision_service import (
    ProposalConflictError, ProposalNotFoundError, RecutDecisionService)
from repository import process_events
from repository.batch_runs import BatchRunRepository
from repository.image_extras import ImageExtrasRepository
from repository.popularity import PopularityRepository
from Storage.models import (
    CutState, Image, ImageCut, ImageExtras, ImageStatus, MemeTextRating, PopularityAbsorption, PopularitySource)


async def _proposal(session, *, state=CutState.PROPOSED, source_status=ImageStatus.ACTIVE,
                    cut_status=ImageStatus.CUT_PROPOSED, with_cut=True, with_source=True, run_id=None):
    source = Image(filename=f"{uuid.uuid4()}.jpg", status=source_status)
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=cut_status)
    session.add_all([source, cut])
    await session.flush()
    row = ImageCut(
        source_image_id=source.id if with_source else None, cut_image_id=cut.id if with_cut else None,
        crop_x=10, crop_y=20, crop_w=300, crop_h=200, detector="margin_trim", detector_version="1",
        confidence=0.9, state=state, detect_run_id=run_id)
    session.add(row)
    await session.flush()
    return row, source, cut


async def _status(session, image_id):
    return (await session.execute(select(Image.status).where(Image.id == image_id))).scalar_one()


async def _flag(session, image_id):
    return (await ImageExtrasRepository(session).get_flags_bulk([image_id]))[image_id]


async def _remarks(session, image_id):
    return (await session.execute(select(ImageExtras.remarks).where(ImageExtras.image_id == image_id))).scalar_one()


async def _rating(session, image_id, query_hash="h" * 64):
    row = MemeTextRating(query_hash=query_hash, image_id=image_id, grade=2, rank=1, reading="parsed",
                         parser_version="p1", taxonomy_version=1, language="en")
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_activates_the_cut_flags_the_source_and_records_the_decision(db_session):
    row, source, cut = await _proposal(db_session)

    result = await RecutDecisionService(db_session).approve(row.id)

    assert result["id"] == str(row.id) and result["state"] == "approved" and isinstance(result["decidedAt"], str)
    await db_session.refresh(row)
    assert row.state == CutState.APPROVED and row.decided_at is not None
    assert await _status(db_session, cut.id) == ImageStatus.ACTIVE
    assert await _status(db_session, source.id) == ImageStatus.ACTIVE
    assert await _flag(db_session, source.id) is True
    assert await _remarks(db_session, source.id) == "recut_approved"
    assert await _flag(db_session, cut.id) is False
    assert await process_events.subject_has_any_event(db_session, "recut", row.id, ("approved",))


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_flags_the_cut_and_leaves_the_source_alone(db_session):
    row, source, cut = await _proposal(db_session)

    result = await RecutDecisionService(db_session).reject(row.id)

    assert result["state"] == "rejected"
    await db_session.refresh(row)
    assert row.state == CutState.REJECTED and row.decided_at is not None
    assert await _status(db_session, cut.id) == ImageStatus.CUT_PROPOSED
    assert await _flag(db_session, cut.id) is True
    assert await _remarks(db_session, cut.id) == "recut_rejected"
    assert await _flag(db_session, source.id) is False
    assert await process_events.subject_has_any_event(db_session, "recut", row.id, ("rejected",))
    assert not await process_events.subject_has_any_event(db_session, "recut", row.id, ("approved",))


@pytest.mark.asyncio(loop_scope="session")
async def test_the_event_is_attributed_to_the_detect_run(db_session):
    run_id = await BatchRunRepository(db_session).create_run("detect_recut_crops", "manual")
    row, _, _ = await _proposal(db_session, run_id=run_id)

    await RecutDecisionService(db_session).approve(row.id)

    counts = await process_events.count_events(db_session, "recut", [run_id])
    assert ("approved", 1) in [(event, count) for _, event, count in counts]


@pytest.mark.asyncio(loop_scope="session")
async def test_a_second_decision_is_refused_and_changes_nothing(db_session):
    row, source, cut = await _proposal(db_session)
    service = RecutDecisionService(db_session)
    await service.approve(row.id)

    for call in (service.approve, service.reject):
        with pytest.raises(ProposalConflictError) as refused:
            await call(row.id)
        assert refused.value.code == "already_decided"
    await db_session.refresh(row)
    assert row.state == CutState.APPROVED
    assert await _flag(db_session, cut.id) is False


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_id_is_not_found(db_session):
    with pytest.raises(ProposalNotFoundError):
        await RecutDecisionService(db_session).approve(uuid.uuid4())
    with pytest.raises(ProposalNotFoundError):
        await RecutDecisionService(db_session).reject(uuid.uuid4())


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("kind", ["approve", "reject"])
@pytest.mark.parametrize("make", [
    dict(with_cut=False), dict(cut_status=ImageStatus.ACTIVE), dict(cut_status=ImageStatus.REJECTED)])
async def test_a_missing_or_wrong_status_cut_is_refused_before_any_write(db_session, kind, make):
    row, source, cut = await _proposal(db_session, **make)

    with pytest.raises(ProposalConflictError) as refused:
        await getattr(RecutDecisionService(db_session), kind)(row.id)

    assert refused.value.code == "cut_missing"
    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED and row.decided_at is None
    assert await _flag(db_session, source.id) is False and await _flag(db_session, cut.id) is False


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("make, code", [
    (dict(with_source=False), "source_missing"),
    (dict(source_status=ImageStatus.PENDING), "source_not_active"),
    (dict(source_status=ImageStatus.CUT_PROPOSED), "source_not_active"),
])
async def test_approve_needs_an_active_source(db_session, make, code):
    row, source, cut = await _proposal(db_session, **make)

    with pytest.raises(ProposalConflictError) as refused:
        await RecutDecisionService(db_session).approve(row.id)

    assert refused.value.code == code
    assert await _status(db_session, cut.id) == ImageStatus.CUT_PROPOSED
    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_refuses_a_source_that_is_already_flagged(db_session):
    row, source, cut = await _proposal(db_session)
    await ImageExtrasRepository(db_session).set_flagged(source.id, True, "duplicate_review")

    with pytest.raises(ProposalConflictError) as refused:
        await RecutDecisionService(db_session).approve(row.id)

    assert refused.value.code == "source_flagged"
    assert await _status(db_session, cut.id) == ImageStatus.CUT_PROPOSED


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_works_without_a_source(db_session):
    row, _, cut = await _proposal(db_session, with_source=False)

    result = await RecutDecisionService(db_session).reject(row.id)

    assert result["state"] == "rejected" and await _flag(db_session, cut.id) is True


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_moves_the_source_ratings_to_the_cut(db_session):
    row, source, cut = await _proposal(db_session)
    rating = await _rating(db_session, source.id)

    await RecutDecisionService(db_session).approve(row.id)

    await db_session.refresh(rating)
    assert rating.image_id == cut.id


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_keeps_the_ratings_with_the_source(db_session):
    row, source, cut = await _proposal(db_session)
    rating = await _rating(db_session, source.id)

    await RecutDecisionService(db_session).reject(row.id)

    await db_session.refresh(rating)
    assert rating.image_id == source.id


@pytest.mark.asyncio(loop_scope="session")
async def test_approve_transfers_the_sources_popularity_to_the_cut(db_session):
    row, source, cut = await _proposal(db_session)
    copy = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    db_session.add(copy)
    await db_session.flush()
    await PopularityRepository(db_session).absorb(copy.id, source.id, PopularitySource.FILE_DUPLICATES)
    await db_session.refresh(source)
    assert source.popularity == 2

    await RecutDecisionService(db_session).approve(row.id)

    await db_session.refresh(cut)
    await db_session.refresh(source)
    assert cut.popularity == 2 and source.popularity == 0
    edge = (await db_session.execute(select(PopularityAbsorption).where(
        PopularityAbsorption.loser_image_id == source.id, PopularityAbsorption.winner_image_id == cut.id)
    )).scalar_one()
    assert edge.source == PopularitySource.RECUT and edge.weight == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_a_popularity_failure_does_not_block_the_approval(db_session, monkeypatch):
    async def broken(self, *args, **kwargs):
        raise RuntimeError("popularity down")
    monkeypatch.setattr(PopularityRepository, "absorb", broken)
    row, source, cut = await _proposal(db_session)

    result = await RecutDecisionService(db_session).approve(row.id)

    assert result["state"] == "approved"
    assert await _status(db_session, cut.id) == ImageStatus.ACTIVE and await _flag(db_session, source.id) is True


def test_the_proposal_lock_is_a_row_lock():
    # The integration fixture runs on one savepointed connection, so a two-connection race cannot be staged here.
    from Backend.app.repositories import recut_repository
    statement = recut_repository.lock_statement(uuid.uuid4())
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
```

- [ ] **Step 2: Run to verify it fails**

Run (repo-root of the worktree): `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_recut_decision_service.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'Backend.app.services.recut_decision_service'`.

- [ ] **Step 3: Add the repository helpers**

In `Backend/app/repositories/recut_repository.py` change the imports to `import uuid`, `from datetime import datetime, timezone`, `from sqlalchemy import func, select, update`, and `from Storage.models import CutState, Image, ImageCut, MemeTextRating`. Update the class docstring to "Review queue (R4, read) and decision helpers (R5a). Never commits." Add at module level (after `_item`):

```python
def lock_statement(cut_id: uuid.UUID):
    """The proposal row with a row lock: two deciders of one proposal take turns, the second sees the first's state."""
    return select(ImageCut).where(ImageCut.id == cut_id).with_for_update()
```

and these methods on `RecutRepository`:

```python
    async def lock_proposal(self, cut_id: uuid.UUID) -> ImageCut | None:
        return (await self.session.execute(lock_statement(cut_id))).scalar_one_or_none()

    async def images_by_id(self, ids: list[uuid.UUID]) -> dict[uuid.UUID, Image]:
        rows = (await self.session.execute(select(Image).where(Image.id.in_(ids)))).scalars().all()
        return {row.id: row for row in rows}

    async def set_image_status(self, image_id: uuid.UUID, status: str) -> None:
        await self.session.execute(update(Image).where(Image.id == image_id).values(status=status))

    async def move_ratings(self, from_id: uuid.UUID, to_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(MemeTextRating).where(MemeTextRating.image_id == from_id).values(image_id=to_id))
        return result.rowcount

    async def mark_decided(self, row: ImageCut, state: str) -> datetime:
        decided_at = datetime.now(timezone.utc)
        row.state = state
        row.decided_at = decided_at
        await self.session.flush()
        return decided_at
```

- [ ] **Step 4: Write the service**

`Backend/app/services/recut_decision_service.py`:

```python
"""Approve or reject one recut proposal (epic screenshot-recut, R5a). One decision is one transaction owned by the caller
(never commits); every precondition is checked before the first write, so a refusal leaves nothing half applied.
No file is moved here: removal is the existing move_flagged then unregister_deleted_images path."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.recut_repository import RecutRepository
from Backend.app.services.business_metrics_service import BusinessMetricsService, ConflictingEventError
from repository.image_extras import ImageExtrasRepository
from repository.popularity import PopularityRepository, guarded
from Storage.models import CutState, ImageCut, ImageStatus, PopularitySource


class ProposalNotFoundError(Exception):
    pass


class ProposalConflictError(Exception):
    """The proposal cannot be decided now; `code` says why (the API sends it as the 409 detail)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RecutDecisionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = RecutRepository(session)
        self.extras = ImageExtrasRepository(session)

    async def approve(self, cut_id: uuid.UUID) -> dict:
        row, cut_image_id, images = await self._prepare(cut_id)
        source = images.get(row.source_image_id) if row.source_image_id is not None else None
        if source is None:
            raise ProposalConflictError("source_missing")
        if source.status != ImageStatus.ACTIVE:
            raise ProposalConflictError("source_not_active")
        if (await self.extras.get_flags_bulk([source.id]))[source.id]:
            raise ProposalConflictError("source_flagged")

        await self.repo.set_image_status(cut_image_id, ImageStatus.ACTIVE)
        await self.extras.set_flagged(source.id, True, "recut_approved")
        # The cut is the same meme as its source: it takes over the source's popularity (weight-0 edge).
        await guarded(self.session, "recut", lambda: PopularityRepository(self.session).absorb(
            source.id, cut_image_id, PopularitySource.RECUT, row.detect_run_id))
        await self.repo.move_ratings(source.id, cut_image_id)
        return await self._finish(row, CutState.APPROVED, "approved")

    async def reject(self, cut_id: uuid.UUID) -> dict:
        row, cut_image_id, _ = await self._prepare(cut_id)
        await self.extras.set_flagged(cut_image_id, True, "recut_rejected")
        return await self._finish(row, CutState.REJECTED, "rejected")

    async def _prepare(self, cut_id: uuid.UUID):
        row = await self.repo.lock_proposal(cut_id)
        if row is None:
            raise ProposalNotFoundError(str(cut_id))
        if row.state != CutState.PROPOSED:
            raise ProposalConflictError("already_decided")
        wanted = [i for i in (row.source_image_id, row.cut_image_id) if i is not None]
        images = await self.repo.images_by_id(wanted) if wanted else {}
        cut = images.get(row.cut_image_id) if row.cut_image_id is not None else None
        if cut is None or cut.status != ImageStatus.CUT_PROPOSED:
            raise ProposalConflictError("cut_missing")
        return row, cut.id, images

    async def _finish(self, row: ImageCut, state: str, event: str) -> dict:
        decided_at = await self.repo.mark_decided(row, state)
        try:
            await BusinessMetricsService(self.session).record("recut", event, row.detect_run_id, row.id)
        except ConflictingEventError:
            raise ProposalConflictError("already_decided") from None
        return {"id": str(row.id), "state": state, "decidedAt": decided_at.isoformat()}
```

- [ ] **Step 5: Run to verify it passes**

Run the Step 2 command. Expected: all tests pass. If `test_approve_transfers_the_sources_popularity_to_the_cut` fails on the numbers, read `PopularityRepository.absorb` and `tree_weight` before touching the test (the cut should end with `1 + tree_weight(source)`); if `BatchRunRepository.create_run` commits and breaks the savepoint fixture, create the `BatchRun` row directly with the session instead and ledger it.

- [ ] **Step 6: Run the whole integration root, then commit**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/ -q`
Expected: all pass (875 before this task plus the new tests).

```bash
git add Backend/app/repositories/recut_repository.py Backend/app/services/recut_decision_service.py tests/integration/test_recut_decision_service.py
git commit -m "feat(recut): R5a decision service

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Decision endpoints and API reference

**Files:**
- Modify: `Backend/app/api/recut.py`, `backend_api.md`
- Test: `Backend/tests/test_recut_endpoints.py`

**Interfaces:**
- Consumes: `RecutDecisionService`, `ProposalNotFoundError`, `ProposalConflictError` (Task 2); `Backend.app.types.generated.recutdecision.Schema` (Task 1).
- Produces: `get_recut_decision_service` dependency; `POST /api/recut/proposals/{proposal_id}/approve` and `/reject`.

- [ ] **Step 1: Write the failing tests**

Append to `Backend/tests/test_recut_endpoints.py` (add the imports `from Backend.app.services.recut_decision_service import ProposalConflictError, ProposalNotFoundError` and `get_recut_decision_service` to the existing `from Backend.app.api.recut import ...`):

```python
DECISION = {"id": "c1", "state": "approved", "decidedAt": "2026-10-29T10:00:00+00:00"}


@pytest.fixture
def client_and_service():
    service = type("S", (), {})()
    service.approve = AsyncMock(return_value=DECISION)
    service.reject = AsyncMock(return_value={**DECISION, "state": "rejected"})
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_recut_decision_service] = lambda: service
    return TestClient(app), service


@pytest.mark.parametrize("verb, state", [("approve", "approved"), ("reject", "rejected")])
def test_decision_returns_the_outcome(client_and_service, verb, state):
    client, service = client_and_service
    cut_id = uuid.uuid4()
    response = client.post(f"/api/recut/proposals/{cut_id}/{verb}")
    assert response.status_code == 200
    assert response.json() == {**DECISION, "state": state}
    getattr(service, verb).assert_awaited_once_with(cut_id)


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_decision_unknown_proposal_is_404(client_and_service, verb):
    client, service = client_and_service
    getattr(service, verb).side_effect = ProposalNotFoundError("x")
    response = client.post(f"/api/recut/proposals/{uuid.uuid4()}/{verb}")
    assert response.status_code == 404 and response.json() == {"detail": "Proposal not found"}


@pytest.mark.parametrize("verb", ["approve", "reject"])
@pytest.mark.parametrize("code", ["already_decided", "cut_missing", "source_missing", "source_not_active", "source_flagged"])
def test_decision_conflict_carries_the_reason_code(client_and_service, verb, code):
    client, service = client_and_service
    getattr(service, verb).side_effect = ProposalConflictError(code)
    response = client.post(f"/api/recut/proposals/{uuid.uuid4()}/{verb}")
    assert response.status_code == 409 and response.json() == {"detail": code}


@pytest.mark.parametrize("verb", ["approve", "reject"])
def test_decision_with_an_id_that_is_not_a_uuid_is_422(client_and_service, verb):
    client, service = client_and_service
    assert client.post(f"/api/recut/proposals/not-a-uuid/{verb}").status_code == 422
    getattr(service, verb).assert_not_awaited()
```

- [ ] **Step 2: Run to verify it fails**

Run from `Backend/`: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_recut_endpoints.py -v`
Expected: ImportError on `get_recut_decision_service`.

- [ ] **Step 3: Add the routes**

In `Backend/app/api/recut.py` add imports `from Backend.app.services.recut_decision_service import ProposalConflictError, ProposalNotFoundError, RecutDecisionService` and `from Backend.app.types.generated.recutdecision import Schema as RecutDecision`, then:

```python
async def get_recut_decision_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[RecutDecisionService, None]:
    yield RecutDecisionService(db)


async def _decide(call, proposal_id: uuid.UUID) -> dict:
    # Raising after the service wrote anything is safe: get_async_db rolls the whole request back.
    try:
        return await call(proposal_id)
    except ProposalNotFoundError:
        raise HTTPException(status_code=404, detail="Proposal not found") from None
    except ProposalConflictError as error:
        raise HTTPException(status_code=409, detail=error.code) from None


@router.post("/proposals/{proposal_id}/approve", response_model=RecutDecision)
async def approve_proposal(proposal_id: uuid.UUID, service: RecutDecisionService = Depends(get_recut_decision_service)):
    return await _decide(service.approve, proposal_id)


@router.post("/proposals/{proposal_id}/reject", response_model=RecutDecision)
async def reject_proposal(proposal_id: uuid.UUID, service: RecutDecisionService = Depends(get_recut_decision_service)):
    return await _decide(service.reject, proposal_id)
```

- [ ] **Step 4: Run to verify it passes, then the whole Backend root**

Run from `Backend/`: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/test_recut_endpoints.py -v`, then `... -m pytest -q`.
Expected: the new tests pass; the full root passes except the known unrelated failure `tests/test_batch_subprocess.py::test_forces_utf8_output_encoding_in_child_env` (report it by name).

- [ ] **Step 5: Document the endpoints**

In `backend_api.md`, "Recut review" section: replace "Read-only; the approve and reject endpoints arrive with R5." with "The review page decides a proposal with the two POST endpoints below; a decision moves no file (removal is `move_flagged`, then `unregister_deleted_images`)." and add after "One proposal":

```
**Approve or reject a proposal**

- **URL**: `/api/recut/proposals/{id}/approve` and `/api/recut/proposals/{id}/reject`
- **Method**: `POST`, no body
- **Response** `200`: `RecutDecision`: `id`, `state` (`approved` or `rejected`) and `decidedAt` (ISO 8601).
  - Approve: the cut becomes `active`, the source is flagged (remarks `recut_approved`) for `move_flagged`, the cut takes over the source's popularity and text ratings, and the `approved` event is recorded.
  - Reject: the cut is flagged (remarks `recut_rejected`), the source is untouched, and the `rejected` event is recorded.
- **Error Responses**: `404` for an unknown id; `422` for an `id` that is not a UUID; `409` with `detail` one of `already_decided` (the proposal is not `proposed`), `cut_missing` (no cut image, or it is not `cut_proposed`), and for approve only `source_missing`, `source_not_active`, `source_flagged` (the source is already flagged for removal). Nothing is changed on a `409`.
```

- [ ] **Step 6: Commit**

```bash
git add Backend/app/api/recut.py Backend/tests/test_recut_endpoints.py backend_api.md
git commit -m "feat(recut): R5a approve and reject endpoints

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: unregister_deleted_images for flagged cuts

**Files:**
- Modify: `repository/images.py`, `batch/tasks/SourceTasks.py`, `docs/image-status.md`, `CLAUDE.md`
- Test: `tests/integration/test_unregister_cut_proposed.py`

**Interfaces:**
- Consumes: `ImagesRepository(session)` (`repository/images.py`), `UnregisterNonExisting(session, base_path)` (`batch/tasks/SourceTasks.py`), `ImageStatus`.
- Produces: `ImagesRepository.get_flagged_images(status: str)` returning rows `(filename, id)` of images of that status whose `image_extras.flagged` is true.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_unregister_cut_proposed.py`:

```python
"""UnregisterNonExisting also reconciles flagged cut_proposed images (screenshot recut R5a): a rejected cut whose file
move_flagged has moved away loses its row, an undecided cut never does. Requires a live PostgreSQL instance."""
import uuid

import pytest
from sqlalchemy import select

from batch.tasks.SourceTasks import UnregisterNonExisting
from repository.image_extras import ImageExtrasRepository
from Storage.models import CutState, Image, ImageCut, ImageStatus


async def _cut_pair(session, tmp_path, *, flagged, file_exists):
    source = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.ACTIVE)
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    session.add_all([source, cut])
    await session.flush()
    (tmp_path / source.filename).write_bytes(b"x")
    if file_exists:
        (tmp_path / cut.filename).write_bytes(b"x")
    row = ImageCut(source_image_id=source.id, cut_image_id=cut.id, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
                   detector="margin_trim", detector_version="1", state=CutState.REJECTED if flagged else CutState.PROPOSED)
    session.add(row)
    if flagged:
        await ImageExtrasRepository(session).set_flagged(cut.id, True, "recut_rejected")
    await session.flush()
    return row, source, cut


async def _exists(session, image_id):
    return (await session.execute(select(Image.id).where(Image.id == image_id))).scalar_one_or_none() is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flagged_cut_whose_file_is_gone_is_removed_and_its_proposal_survives(db_session, tmp_path):
    row, source, cut = await _cut_pair(db_session, tmp_path, flagged=True, file_exists=False)

    await UnregisterNonExisting(db_session, str(tmp_path)).run()

    assert not await _exists(db_session, cut.id)
    assert await _exists(db_session, source.id)
    await db_session.refresh(row)
    assert row.state == CutState.REJECTED and row.cut_image_id is None


@pytest.mark.asyncio(loop_scope="session")
async def test_an_undecided_cut_with_a_missing_file_is_kept(db_session, tmp_path):
    row, source, cut = await _cut_pair(db_session, tmp_path, flagged=False, file_exists=False)

    await UnregisterNonExisting(db_session, str(tmp_path)).run()

    assert await _exists(db_session, cut.id)
    await db_session.refresh(row)
    assert row.state == CutState.PROPOSED and row.cut_image_id == cut.id


@pytest.mark.asyncio(loop_scope="session")
async def test_a_flagged_cut_whose_file_is_still_there_is_kept(db_session, tmp_path):
    _, _, cut = await _cut_pair(db_session, tmp_path, flagged=True, file_exists=True)

    await UnregisterNonExisting(db_session, str(tmp_path)).run()

    assert await _exists(db_session, cut.id)
```

(`run()` calls `delete_images`, which commits the session; with the fixture's savepoint mode that releases the savepoint only. If a test sees rows from a previous test, make the assertions about the seeded ids only, as above.)

- [ ] **Step 2: Run to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_unregister_cut_proposed.py -v`
Expected: the first test fails (`cut` still exists); the other two pass already.

- [ ] **Step 3: Implement**

`repository/images.py`: add `ImageExtras` to the model import if it is not imported, and next to `get_all_images`:

```python
    async def get_flagged_images(self, status: str):
        """(filename, id) of the images of `status` that are flagged for removal."""
        query = (
            select(Image.filename, Image.id)
            .join(ImageExtras, ImageExtras.image_id == Image.id)
            .where(Image.status == status, ImageExtras.flagged == True)
        )
        return (await self.session.execute(query)).all()
```

`batch/tasks/SourceTasks.py`: import `from Storage.models import ImageStatus`, and in `run()` replace `images = await self.repo.get_all_images()` with:

```python
        images = list(await self.repo.get_all_images())
        # A rejected recut cut is flagged and moved by move_flagged; an undecided one (not flagged) is never
        # unregistered, so an unplugged drive cannot wipe the review queue.
        images += list(await self.repo.get_flagged_images(ImageStatus.CUT_PROPOSED))
```

- [ ] **Step 4: Run to verify it passes, then the whole integration root**

Run the Step 2 command (expected 3 passed), then `... -m pytest tests/integration/ -q` (expected all pass), then `... -m pytest batch/tests/ -q` (expected all pass).

- [ ] **Step 5: Docs**

`docs/image-status.md`: in the `cut_proposed to removed` bullet replace the sentence starting "Caveat for R5:" with "`unregister_deleted_images` reconciles `cut_proposed` images that are flagged (never an unflagged one), so the rejected cut's row goes once `move_flagged` has moved its file; the `image_cuts` row stays as `rejected`." In the Statuses table, `cut_proposed` row, "Set by": append "; reject keeps it `cut_proposed` and flags it, approval sets `active` (R5a)". Do not break the `tests/docs/` guards (they read the table and the `--status` list).
`CLAUDE.md`: in the `unregister_deleted_images` entry add: "It also deletes flagged `cut_proposed` images whose file is gone (rejected recut cuts), never an unflagged one."

- [ ] **Step 6: Run the docs guards and commit**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/docs/ -q` (expected 12 passed).

```bash
git add repository/images.py batch/tasks/SourceTasks.py tests/integration/test_unregister_cut_proposed.py docs/image-status.md CLAUDE.md
git commit -m "feat(recut): R5a unregister flagged cut_proposed images

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend API client

**Files:**
- Modify: `Frontend/memes-frontend/src/api/errors.ts`, `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/recut/types.ts`, `src/recut/fixtures.ts`
- Test: `Frontend/memes-frontend/src/api/http/HttpMemesApi.recut.test.ts`

**Interfaces:**
- Consumes: generated `RecutDecision` (Task 1).
- Produces: `CuratorRequestError(status, code?)` with `readonly code?: string`; `MemesApi.approveRecutProposal(id): Promise<RecutDecision>` and `rejectRecutProposal(id)`; fixture `makeDecision(id, state = "approved")`; type re-export `RecutDecision` from `src/recut/types.ts`.

- [ ] **Step 1: Write the failing tests**

Append to `HttpMemesApi.recut.test.ts` inside the `describe("recut API")` block (import `makeDecision` from `../../recut/fixtures`):

```ts
  it.each([["approve", "approveRecutProposal"], ["reject", "rejectRecutProposal"]] as const)(
    "posts a %s decision",
    async (verb, method) => {
      const body = makeDecision("a", verb === "approve" ? "approved" : "rejected")
      const fetchMock = stubFetch({ ok: true, status: 200, body })

      expect(await api[method]("a")).toEqual(body)
      expect(fetchMock.mock.calls[0][0]).toBe(`http://backend/api/recut/proposals/a/${verb}`)
      expect(fetchMock.mock.calls[0][1].method).toBe("POST")
    })

  it("carries the 409 reason code on the error", async () => {
    stubFetch({ ok: false, status: 409, body: { detail: "already_decided" } })

    await expect(api.approveRecutProposal("a")).rejects.toMatchObject({ status: 409, code: "already_decided" })
  })

  it("has no code for a failure without a string detail", async () => {
    stubFetch({ ok: false, status: 500, body: undefined })

    const error = await api.rejectRecutProposal("a").catch((e) => e)
    expect(error).toBeInstanceOf(CuratorRequestError)
    expect(error.status).toBe(500)
    expect(error.code).toBeUndefined()
  })
```

- [ ] **Step 2: Run to verify it fails**

Run from `Frontend/memes-frontend/`: `pnpm exec vitest run src/api/http/HttpMemesApi.recut.test.ts`
Expected: failures (`makeDecision` not exported / methods undefined).

- [ ] **Step 3: Implement**

- `errors.ts`: `CuratorRequestError` gets `readonly code?: string` and `constructor(status: number, code?: string)` assigning both (message unchanged).
- `recut/types.ts`: add `RecutDecision` to the import and the `export type`.
- `recut/fixtures.ts`: `export function makeDecision(id: string, state: string = "approved"): RecutDecision { return { id, state, decidedAt: "2026-10-29T10:00:00+00:00" } }` (import the type).
- `MemesApi.ts`: import `RecutDecision` with the other recut types and add `approveRecutProposal(id: string): Promise<RecutDecision>` and `rejectRecutProposal(id: string): Promise<RecutDecision>` after `getRecutProposal`.
- `HttpMemesApi.ts`: import `RecutDecision`; after `getRecutProposal` add

```ts
  approveRecutProposal(id: string): Promise<RecutDecision> {
    return this.decideRecutProposal(id, "approve")
  }

  rejectRecutProposal(id: string): Promise<RecutDecision> {
    return this.decideRecutProposal(id, "reject")
  }

  private async decideRecutProposal(id: string, verb: "approve" | "reject"): Promise<RecutDecision> {
    const response = await fetch(`${this.baseUrl}/api/recut/proposals/${encodeURIComponent(id)}/${verb}`, {
      method: "POST",
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status, await readDetailCode(response))
    return response.json()
  }
```

and a module-level helper next to `readValidationMessages`:

```ts
/** The reason code of a 409 (FastAPI's string `detail`); undefined when the body has none. */
async function readDetailCode(response: Response): Promise<string | undefined> {
  try {
    const detail = (await response.json())?.detail
    return typeof detail === "string" ? detail : undefined
  } catch {
    return undefined
  }
}
```

- `test/mockApi.ts`: after the recut defaults add `approveRecutProposal: vi.fn().mockResolvedValue(makeDecision('p1', 'approved')),` and `rejectRecutProposal: vi.fn().mockResolvedValue(makeDecision('p1', 'rejected')),` (import `makeDecision`).

- [ ] **Step 4: Run to verify it passes, then type-check and lint**

Run: `pnpm exec vitest run src/api/http/HttpMemesApi.recut.test.ts` (expected pass), `pnpm exec tsc -b` (clean), `pnpm exec eslint src/ --max-warnings 0` (clean).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(recut): R5a decision calls in the frontend API client

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Queue hook and review page

**Files:**
- Modify: `Frontend/memes-frontend/src/recut/useRecutQueue.ts`, `src/pages/RecutReviewPage.tsx`
- Test: `Frontend/memes-frontend/src/pages/RecutReviewPage.test.tsx`

**Interfaces:**
- Consumes: `memesApi.approveRecutProposal/rejectRecutProposal`, `CuratorRequestError.code` (Task 5).
- Produces: from `useRecutQueue`: `decision: DecisionState`, `decide(kind: "approve" | "reject"): Promise<DecideResult>`, with `export type DecisionState = { status: "idle" } | { status: "saving" } | { status: "failed" } | { status: "conflict"; code: string }` and `export type DecideResult = { outcome: "ignored" | "failed" | "conflict" } | { outcome: "done"; next: string | null }`.

- [ ] **Step 1: Write the failing tests**

In `RecutReviewPage.test.tsx` replace the test "keeps approve and reject visible but disabled" with the following (add `makeDecision` to the fixtures import; the default `setup()` serves `a`, `b`, `c` with `previousId`/`nextId` chaining):

```tsx
  it("approves with the button, opens the next proposal and shrinks the queue", async () => {
    const user = userEvent.setup()
    const api = setup()
    api.approveRecutProposal = vi.fn().mockResolvedValue(makeDecision("a", "approved"))
    await screen.findByText("1 of 3")

    await user.click(screen.getByRole("button", { name: "Approve" }))

    expect(api.approveRecutProposal).toHaveBeenCalledWith("a")
    expect(await screen.findByText("1 of 2")).toBeInTheDocument()
    expect(screen.getByTestId("where")).toHaveTextContent("?cut=b")
    expect(screen.queryByRole("button", { name: "Open proposal 3" })).not.toBeInTheDocument()
  })

  it("rejects with the R key and approves with the A key", async () => {
    const api = setup()
    api.rejectRecutProposal = vi.fn().mockResolvedValue(makeDecision("a", "rejected"))
    api.approveRecutProposal = vi.fn().mockResolvedValue(makeDecision("b", "approved"))
    await screen.findByText("1 of 3")

    fireEvent.keyDown(window, { key: "r" })
    expect(await screen.findByText("1 of 2")).toBeInTheDocument()
    expect(api.rejectRecutProposal).toHaveBeenCalledWith("a")

    fireEvent.keyDown(window, { key: "a" })
    await waitFor(() => expect(api.approveRecutProposal).toHaveBeenCalledWith("b"))
  })

  it("ignores A and R with a modifier key", async () => {
    const api = setup()
    await screen.findByText("1 of 3")

    fireEvent.keyDown(window, { key: "a", ctrlKey: true })
    fireEvent.keyDown(window, { key: "r", metaKey: true })

    expect(api.approveRecutProposal).not.toHaveBeenCalled()
    expect(api.rejectRecutProposal).not.toHaveBeenCalled()
  })

  it("opens the previous proposal after deciding the last one", async () => {
    const user = userEvent.setup()
    const api = setup({}, "/admin/recut?cut=c")
    api.approveRecutProposal = vi.fn().mockResolvedValue(makeDecision("c", "approved"))
    await screen.findByText("3 of 3")

    await user.click(screen.getByRole("button", { name: "Approve" }))

    expect(await screen.findByText("2 of 2")).toBeInTheDocument()
    expect(screen.getByTestId("where")).toHaveTextContent("?cut=b")
  })

  it("shows the empty queue after deciding the only proposal", async () => {
    const user = userEvent.setup()
    const api = setup({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a"], 1)),
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a")) })
    api.rejectRecutProposal = vi.fn().mockResolvedValue(makeDecision("a", "rejected"))
    await screen.findByText("1 of 1")

    await user.click(screen.getByRole("button", { name: "Reject" }))

    expect(await screen.findByText("No proposals waiting for review")).toBeInTheDocument()
  })

  it("sends one request and disables both buttons while a decision is saving", async () => {
    const user = userEvent.setup()
    let finish: (value: ReturnType<typeof makeDecision>) => void = () => {}
    const api = setup()
    api.approveRecutProposal = vi.fn().mockReturnValue(new Promise((resolve) => { finish = resolve }))
    await screen.findByText("1 of 3")

    await user.click(screen.getByRole("button", { name: "Approve" }))
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled()
    fireEvent.keyDown(window, { key: "a" })
    fireEvent.keyDown(window, { key: "r" })
    expect(api.approveRecutProposal).toHaveBeenCalledTimes(1)
    expect(api.rejectRecutProposal).not.toHaveBeenCalled()

    finish(makeDecision("a", "approved"))
    expect(await screen.findByText("1 of 2")).toBeInTheDocument()
  })

  it("explains a 409, reloads the queue and moves to the first remaining proposal", async () => {
    const user = userEvent.setup()
    const list = vi.fn()
      .mockResolvedValueOnce(makeProposalPage(["a", "b", "c"], 3))
      .mockResolvedValue(makeProposalPage(["b", "c"], 2))
    const api = setup({ listRecutProposals: list })
    api.approveRecutProposal = vi.fn().mockRejectedValue(new CuratorRequestError(409, "already_decided"))
    await screen.findByText("1 of 3")

    await user.click(screen.getByRole("button", { name: "Approve" }))

    expect(await screen.findByText("Already decided")).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("?cut=b"))
    expect(await screen.findByText("1 of 2")).toBeInTheDocument()
    expect(list).toHaveBeenCalledTimes(2)
  })

  it("says why a flagged source cannot be approved", async () => {
    const user = userEvent.setup()
    const api = setup()
    api.approveRecutProposal = vi.fn().mockRejectedValue(new CuratorRequestError(409, "source_flagged"))
    await screen.findByText("1 of 3")

    await user.click(screen.getByRole("button", { name: "Approve" }))

    expect(await screen.findByText("The source image is flagged, so it can't be approved")).toBeInTheDocument()
  })

  it("keeps the proposal open and lets the owner try again after a failure", async () => {
    const user = userEvent.setup()
    const api = setup()
    api.approveRecutProposal = vi.fn()
      .mockRejectedValueOnce(new CuratorRequestError(500))
      .mockResolvedValue(makeDecision("a", "approved"))
    await screen.findByText("1 of 3")

    await user.click(screen.getByRole("button", { name: "Approve" }))
    expect(await screen.findByText("Could not save the decision")).toBeInTheDocument()
    expect(screen.getByText("1 of 3")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled()

    await user.click(screen.getByRole("button", { name: "Approve" }))
    expect(await screen.findByText("1 of 2")).toBeInTheDocument()
    expect(screen.queryByText("Could not save the decision")).not.toBeInTheDocument()
  })

  it("keeps the buttons disabled for a proposal that is no longer proposed", async () => {
    setup({ getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("a", { state: "approved" })) })
    await screen.findByText("1 of 3")
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled()
  })
```

Note `setup()` returns the mock API; replacing a method on the returned object after render works because the page calls `memesApi.approveRecutProposal` at click time. If the hook captured the function at render, assign before render instead by passing it in the `setup` overrides. Remove the now-false hint expectation (`/arrives with the approve\/reject step/`) with the old test.

- [ ] **Step 2: Run to verify it fails**

Run: `pnpm exec vitest run src/pages/RecutReviewPage.test.tsx`
Expected: the new tests fail (buttons disabled, no keys); the R4 tests still pass.

- [ ] **Step 3: Implement the hook**

In `useRecutQueue.ts` add the types from Interfaces, a `decision` state (`useState<DecisionState>({ status: "idle" })`), `const decidingRef = useRef(false)`, make `reload` a `useCallback(() => {...}, [])`, and add:

```ts
  const decide = useCallback(async (kind: "approve" | "reject"): Promise<DecideResult> => {
    const current = loaded !== null && loaded.id === openId && loaded.state.status === "ready" ? loaded.state.data : null
    if (current === null || current.state !== "proposed" || decidingRef.current) return { outcome: "ignored" }
    decidingRef.current = true
    setDecision({ status: "saving" })
    try {
      if (kind === "approve") await memesApi.approveRecutProposal(current.id)
      else await memesApi.rejectRecutProposal(current.id)
      setList((previous) => ({
        ...previous,
        items: previous.items.filter((item) => item.id !== current.id),
        total: Math.max(0, previous.total - 1),
      }))
      setLoaded(null)
      setDecision({ status: "idle" })
      return { outcome: "done", next: current.nextId ?? current.previousId }
    } catch (error) {
      if (error instanceof CuratorRequestError && error.status === 409) {
        setDecision({ status: "conflict", code: error.code ?? "" })
        reload()
        return { outcome: "conflict" }
      }
      setDecision({ status: "failed" })
      return { outcome: "failed" }
    } finally {
      decidingRef.current = false
    }
  }, [memesApi, loaded, openId, reload])
```

and return `decision` and `decide` from the hook.

- [ ] **Step 4: Implement the page**

In `RecutReviewPage.tsx`:

- `const { items, total, listStatus, loadMore, detail, decision, decide } = queue`; `const saving = decision.status === "saving"`; `const decidable = detail?.status === "ready" && detail.data.state === "proposed" && !saving`.
- `async function onDecide(kind: "approve" | "reject") { const result = await decide(kind); if (result.outcome === "done" && result.next !== null) open(result.next) }` (a plain handler; `decide` is stable per render).
- Key handler (same effect as the arrows; add to its dependency list `decidable` and `onDecide`): after the arrow lines, `if (decidable && event.key.toLowerCase() === "a") void onDecide("approve")` and `... "r" ... "reject"`. The modifier and INPUT/TEXTAREA guards above already return early. If the React Compiler lint rejects calling `onDecide` from the effect, inline the call through a `useCallback`ed `onDecide`.
- Conflict follow-up effect:

```tsx
  const conflicted = decision.status === "conflict"
  useEffect(() => {
    if (!conflicted || listStatus !== "ready" || firstId === null || firstId === openId) return
    open(firstId, true)
  }, [conflicted, listStatus, firstId, openId, open])
```

- Buttons: `disabled={!decidable}` and `onClick={() => void onDecide("approve")}` / `"reject"`; remove the "Deciding arrives..." span; add `aria-keyshortcuts="a"` / `"r"` to the buttons.
- Alert above `{body}` in the returned JSX:

```tsx
      {decision.status === "failed" && <p role="alert" className="mb-3">Could not save the decision</p>}
      {decision.status === "conflict" && <p role="alert" className="mb-3">{CONFLICT_TEXT[decision.code] ?? "This proposal can no longer be applied"}</p>}
```

with, at module level:

```tsx
const CONFLICT_TEXT: Record<string, string> = {
  already_decided: "Already decided",
  source_flagged: "The source image is flagged, so it can't be approved",
}
```

(`source_missing`, `source_not_active`, `cut_missing` and unknown codes use the default sentence "This proposal can no longer be applied".)

- [ ] **Step 5: Run to verify it passes**

Run: `pnpm exec vitest run src/pages/RecutReviewPage.test.tsx` (all pass), then the full gate from `Frontend/memes-frontend/`: `pnpm exec vitest run`, `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`.
Expected: all green; vitest 624 plus the new tests, minus the replaced one.

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(recut): R5a approve and reject on the review page

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Docs, status and full verification

**Files:**
- Modify: `docs/image-derived-data.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r5a-approve-reject-lifecycle-design.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r4-review-api-and-ui-design.md`

- [ ] **Step 1: Edit the docs**

- `docs/image-derived-data.md`: in the `meme_text_ratings` row, change "Moved to the cut on approve, stay with the source on reject" to "Moved to the cut on approve, stay with the source on reject (implemented in R5a)".
- R5a spec: change `Status: planned` to `Status: implementation` (it becomes `done` after the merge). The `Plan:` link is already there.
- Tracker: R5a row status `implementation`; the R4 spec gets a `Follow-ups:` line pointing at the R5a spec (it has none for R5 yet).

- [ ] **Step 2: Full verification**

Run each root separately:
`DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" ...python.exe -m pytest tests/integration/ -q`; from `Backend/`: `...python.exe -m pytest -q` (known unrelated failure: `test_forces_utf8_output_encoding_in_child_env`); `...python.exe -m pytest batch/tests/ -q`; `...python.exe -m pytest tests/rules/ -q`; `...python.exe -m pytest tests/docs/ -q`; from `Frontend/memes-frontend/`: `pnpm exec vitest run`, `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`; from `Frontend/`: `bash generate-types.sh` then `git diff --stat Frontend/memes-frontend/src/types/generated/` (expected: no change).
Smoke: with the repo's Python, import the app and list the OpenAPI paths: `python -c "from Backend.app.main import app; print([p for p in app.openapi()['paths'] if 'recut' in p])"` (needs `DATABASE_URL` set to the test URL); expected: the list/detail paths plus `/api/recut/proposals/{proposal_id}/approve` and `/reject`. Do not bind any server port.

- [ ] **Step 3: Commit**

```bash
git add docs
git commit -m "docs(recut): R5a implementation status

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
