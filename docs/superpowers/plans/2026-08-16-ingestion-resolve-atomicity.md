# Ingestion Resolve Atomicity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `IngestionService.resolve()` apply each decision in a batch independently, so one decision's failure can no longer discard other decisions that already succeeded in the same request, and so a committed "rejected" status is never left describing a file that hasn't actually moved (or vice versa).

**Architecture:** Each decision gets its own commit before its associated file move runs (a deliberate, narrow exception to the single-commit-point convention, scoped to this one method). Per-decision failures are caught and classified into three buckets returned in the response — `failed` (the DB write itself didn't apply, safe to retry as-is), `move_failed` (the DB write committed but the file move failed — self-healing via the existing `undo_reject` endpoint), and the original `rejected`/`kept` for full successes.

**Tech Stack:** FastAPI + SQLAlchemy async (`Backend/app/repositories/ingestion_repository.py`, `Backend/app/services/ingestion_service.py`, `Backend/app/api/ingestion.py`), hand-authored JSON Schema (`shared/schemas/`) driving generated TypeScript (`Frontend/memes-frontend/src/types/generated/all.d.ts`), React + TypeScript (`Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`), pytest (backend unit + integration), vitest + @testing-library/react (frontend).

## Global Constraints

- `IngestionRepository.commit()`/`rollback()` are a deliberate, narrow exception to the "repositories must not call `session.commit()`" rule in `CLAUDE.md` — scoped to `IngestionService.resolve()` only. Do not use them from any other repository method or service.
- The `else: raise HTTPException(422, ...)` branch for an unrecognized `decision` value in `resolve()` stays exactly as-is — it is unreachable through the real API (Pydantic's `Literal["reject", "keep"]` already rejects bad values before the service ever runs) and is not part of this fix.
- No new repair endpoint or background job for the `move_failed` case — `undo_reject()` already handles it correctly for free (see the spec's "Design" section). Do not add one.
- No automatic retry of a failed move or failed DB write inside the backend — surfacing the failure and letting the existing submit flow retry is sufficient.

Spec: `docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md`

---

## Task 1: Backend — per-decision commit, three-bucket result

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py` (add `commit()`/`rollback()` methods)
- Modify: `Backend/app/services/ingestion_service.py:100-118` (rewrite `resolve()`)
- Modify or create: `Backend/tests/test_ingestion_service.py` — **check whether this file already
  exists first** (it may have been created by the sibling `2026-08-16-ingestion-decision-
  staleness-guard` plan's Task 1, which adds a `TestResolveRejectSkipsNonPending` class to it).
  If it exists, add the new test classes below to it. If it does not exist, create it with the
  full content shown in Step 1.
- Create: `tests/integration/test_ingestion_resolve_atomicity.py`

**Interfaces:**
- Consumes: nothing from other tasks in this plan.
- Produces: `IngestionService.resolve(tier: str, decisions: list[dict]) -> dict` now always
  returns a dict with exactly four keys: `rejected: list[str]`, `kept: list[str]`,
  `failed: list[dict]` (each `{"image_id": str, "decision": str, "error": str}`),
  `move_failed: list[dict]` (each `{"image_id": str, "error": str}`). Task 2's Pydantic models
  must match these exact shapes.

- [ ] **Step 1: Write the failing unit tests**

If `Backend/tests/test_ingestion_service.py` does not already exist, create it with this header
and fixtures:

```python
"""
Unit tests for IngestionService.resolve(), mocking IngestionRepository directly.
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from Backend.app.services.ingestion_service import IngestionService


@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo):
    return IngestionService(mock_repo)
```

If it already exists (from the sibling plan), leave the existing header/fixtures/
`TestResolveRejectSkipsNonPending` class untouched and add the classes below to the same file.

Add these test classes:

```python
class TestResolveCommitOrdering:
    async def test_reject_commits_before_move(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.return_value = "file.jpg"
        call_order = []
        mock_repo.commit.side_effect = lambda: call_order.append("commit")

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            mock_image_store.move_to_rejected.side_effect = lambda f: call_order.append("move")
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        assert call_order == ["commit", "move"]
        assert result == {"rejected": [str(image_id)], "kept": [], "failed": [], "move_failed": []}

    async def test_keep_commits_after_mark_reviewed(self, service, mock_repo):
        image_id = uuid.uuid4()

        result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "keep"}])

        mock_repo.mark_reviewed.assert_awaited_once_with(image_id, "tier_a")
        mock_repo.commit.assert_awaited_once()
        assert result == {"rejected": [], "kept": [str(image_id)], "failed": [], "move_failed": []}


class TestResolveMoveFailure:
    async def test_move_failure_after_commit_lands_in_move_failed_not_failed(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.return_value = "file.jpg"

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            mock_image_store.move_to_rejected.side_effect = OSError("file locked")
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        mock_repo.rollback.assert_not_called()  # commit already happened -- nothing to roll back
        assert result["rejected"] == [str(image_id)]
        assert result["move_failed"] == [{"image_id": str(image_id), "error": "file locked"}]
        assert result["failed"] == []


class TestResolveDbFailure:
    async def test_reject_db_failure_rolls_back_and_records_failed(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.side_effect = RuntimeError("db down")

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        mock_repo.rollback.assert_awaited_once()
        mock_image_store.move_to_rejected.assert_not_called()
        assert result["failed"] == [
            {"image_id": str(image_id), "decision": "reject", "error": "db down"}
        ]
        assert result["rejected"] == []

    async def test_keep_db_failure_rolls_back_and_records_failed(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.mark_reviewed.side_effect = RuntimeError("db down")

        result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "keep"}])

        mock_repo.rollback.assert_awaited_once()
        assert result["failed"] == [
            {"image_id": str(image_id), "decision": "keep", "error": "db down"}
        ]
        assert result["kept"] == []


class TestResolvePartialBatch:
    async def test_one_failure_does_not_abort_remaining_decisions(self, service, mock_repo):
        id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.reject_image.side_effect = ["first.jpg", RuntimeError("boom"), "third.jpg"]
        decisions = [
            {"image_id": id1, "decision": "reject"},
            {"image_id": id2, "decision": "reject"},
            {"image_id": id3, "decision": "reject"},
        ]

        with patch("Backend.app.services.ingestion_service.image_store"):
            result = await service.resolve("tier_a", decisions)

        assert result["rejected"] == [str(id1), str(id3)]
        assert result["failed"] == [{"image_id": str(id2), "decision": "reject", "error": "boom"}]
        assert mock_repo.commit.await_count == 2
        assert mock_repo.rollback.await_count == 1

    async def test_unknown_decision_value_still_raises_and_aborts(self, service, mock_repo):
        # Unreachable through the real API (Pydantic validates decision values before the
        # service ever runs) -- this documents that the existing hard-abort behavior for this
        # branch is intentionally unchanged.
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await service.resolve("tier_a", [{"image_id": uuid.uuid4(), "decision": "maybe"}])

    async def test_one_move_failure_does_not_abort_remaining_decisions(self, service, mock_repo):
        id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.reject_image.side_effect = ["first.jpg", "second.jpg", "third.jpg"]
        decisions = [
            {"image_id": id1, "decision": "reject"},
            {"image_id": id2, "decision": "reject"},
            {"image_id": id3, "decision": "reject"},
        ]

        def flaky_move(filename):
            if filename == "second.jpg":
                raise OSError("file locked")

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            mock_image_store.move_to_rejected.side_effect = flaky_move
            result = await service.resolve("tier_a", decisions)

        # All three are "rejected" -- the DB commit succeeded for every one of them, including
        # id2, whose only problem was its file move, not its database write.
        assert result["rejected"] == [str(id1), str(id2), str(id3)]
        assert result["move_failed"] == [{"image_id": str(id2), "error": "file locked"}]
        assert result["failed"] == []
        assert mock_repo.commit.await_count == 3
        assert mock_repo.rollback.await_count == 0


class TestUndoRejectAfterMoveFailure:
    async def test_undo_reject_is_safe_after_a_move_failure(self, service, mock_repo):
        # A move_failed reject leaves the DB durably "rejected" but the file never actually
        # moved to rejected/ -- undo_reject()'s own precondition (status == "rejected") already
        # matches that state, and image_store.move_from_rejected() is a no-op when the file
        # isn't where it expects it, so undoing is safe without any change to undo_reject()
        # itself. This test lets the real image_store.move_from_rejected run (not mocked) to
        # prove that against Backend/tests/conftest.py's BASE_PATH=/tmp/test_images, where the
        # file in question was never actually created.
        image_id = uuid.uuid4()
        mock_repo.undo_reject.return_value = "file-that-was-never-moved.jpg"

        result = await service.undo_reject(image_id)

        assert result == {"image_id": str(image_id), "status": "pending"}
        mock_repo.undo_reject.assert_awaited_once_with(image_id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`

Expected: every test in `TestResolveCommitOrdering`, `TestResolveMoveFailure`,
`TestResolveDbFailure`, and `TestResolvePartialBatch` FAILS — `resolve()`'s current
implementation never calls `mock_repo.commit`/`mock_repo.rollback` at all (no such calls exist
in the code yet), and its return dict has only `rejected`/`kept` keys, not `failed`/
`move_failed`. Two tests already PASS without any code change, and that's expected and correct:
`test_unknown_decision_value_still_raises_and_aborts` (existing behavior, unchanged by this
task) and `TestUndoRejectAfterMoveFailure::test_undo_reject_is_safe_after_a_move_failure`
(`undo_reject()` isn't modified by this plan at all — this test documents that it already
handles the new `move_failed` state correctly, by construction).

- [ ] **Step 3: Add `commit()`/`rollback()` to `IngestionRepository`**

In `Backend/app/repositories/ingestion_repository.py`, add these two methods to the
`IngestionRepository` class (place them after `mark_reviewed`, at the end of the class):

```python
    async def commit(self) -> None:
        """Commits the current transaction. Repositories otherwise never commit --
        get_async_db owns that boundary -- but IngestionService.resolve() needs each decision
        durably applied before its associated file move runs, so a later decision's failure
        can't roll back an earlier decision whose file has already been physically moved. See
        docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md. Not for use
        outside resolve()."""
        await self.session.commit()

    async def rollback(self) -> None:
        """Rolls back the current transaction -- paired with commit() above, same scope and
        same caveat."""
        await self.session.rollback()
```

- [ ] **Step 4: Rewrite `IngestionService.resolve()`**

In `Backend/app/services/ingestion_service.py`, replace the existing `resolve` method
(lines 100-118):

```python
    async def resolve(self, tier: str, decisions: list[dict]) -> dict:
        """Apply per-image reject/keep decisions independently -- one decision's failure (DB or
        filesystem) does not affect any other decision in the same call. `decisions` is a list
        of {"image_id": UUID, "decision": "reject" | "keep"}. Partial resolution is expected --
        callers don't have to decide every member of a cluster in one call, and a partially
        successful batch is a normal outcome, not an error response."""
        rejected, kept, failed, move_failed = [], [], [], []
        for entry in decisions:
            image_id = entry["image_id"]
            decision = entry["decision"]
            try:
                if decision == "reject":
                    filename = await self.repo.reject_image(image_id)
                    if filename is None:
                        continue
                    await self.repo.commit()
                    try:
                        image_store.move_to_rejected(filename)
                    except Exception as move_error:
                        # Broad on purpose: shutil.move can raise shutil.Error (not an OSError
                        # subclass) as well as OSError. Anything from this call must land here,
                        # not fall through to the except below -- the DB commit already
                        # happened, so misclassifying this as `failed` would claim nothing was
                        # applied when the image is, in fact, durably rejected.
                        move_failed.append({"image_id": str(image_id), "error": str(move_error)})
                    rejected.append(str(image_id))
                elif decision == "keep":
                    await self.repo.mark_reviewed(image_id, tier)
                    await self.repo.commit()
                    kept.append(str(image_id))
                else:
                    raise HTTPException(status_code=422, detail=f"Unknown decision: {decision!r}")
            except HTTPException:
                raise
            except Exception as e:
                await self.repo.rollback()
                failed.append({"image_id": str(image_id), "decision": decision, "error": str(e)})
        return {"rejected": rejected, "kept": kept, "failed": failed, "move_failed": move_failed}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`

Expected: PASS — full file (both the new classes and, if the sibling plan's Task 1 already ran,
`TestResolveRejectSkipsNonPending` alongside them).

- [ ] **Step 6: Write the failing integration test**

Create `tests/integration/test_ingestion_resolve_atomicity.py`:

```python
"""
Integration test for IngestionService.resolve()'s per-decision commit behavior -- proves a
later decision's DB failure cannot roll back an earlier decision that already committed. Unit
tests (Backend/tests/test_ingestion_service.py) prove the code *calls* commit/rollback in the
right order; this proves those calls actually protect data against PostgreSQL's own rollback
semantics, which a mocked repository can't verify.

Requires a live PostgreSQL instance with pgvector -- see tests/integration/conftest.py. Safe to
call session.commit()/rollback() here: db_session is bound with
join_transaction_mode="create_savepoint", so these become nested SAVEPOINTs inside the test's
own outer transaction, which is always rolled back at the end regardless (see conftest.py).
"""
import uuid
from unittest.mock import patch

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService
from repository.batch_runs import BatchRunRepository
from Storage.models import Image


async def _make_run(session) -> uuid.UUID:
    return await BatchRunRepository(session).create_run(
        kind="ingestion", trigger="manual", stage="tier_a_review"
    )


async def _make_image(session, status: str, batch_id) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    return image.id


@pytest.mark.asyncio(loop_scope="session")
async def test_earlier_decision_stays_committed_after_a_later_db_failure(db_session):
    batch_id = await _make_run(db_session)
    first_id = await _make_image(db_session, "pending", batch_id)
    second_id = await _make_image(db_session, "pending", batch_id)

    service = IngestionService(IngestionRepository(db_session))

    async def flaky_mark_reviewed(image_id, tier):
        raise RuntimeError("simulated DB failure")

    service.repo.mark_reviewed = flaky_mark_reviewed
    decisions = [
        {"image_id": first_id, "decision": "reject"},
        {"image_id": second_id, "decision": "keep"},
    ]

    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        result = await service.resolve("tier_a", decisions)

    assert result["rejected"] == [str(first_id)]
    assert result["failed"] == [
        {"image_id": str(second_id), "decision": "keep", "error": "simulated DB failure"}
    ]

    first_image = await db_session.get(Image, first_id)
    await db_session.refresh(first_image)  # force a real re-read -- see module docstring
    assert first_image.status == "rejected"
```

- [ ] **Step 7: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_resolve_atomicity.py -v`

Expected: FAIL — before Steps 3-4's implementation, `resolve()` has no per-decision commit, so
`first_id`'s status change is never durably committed on its own; when `second_id`'s
`mark_reviewed` raises and propagates all the way out of the unmodified `resolve()` (no
try/except exists yet), the exception bubbles up through the test itself (not caught), so this
test fails with an unhandled `RuntimeError`, not a clean assertion failure — that's expected at
this point, since Steps 3-4 haven't been applied to this test's environment yet.

Note: if you're running this step after Steps 3-4 are already implemented (e.g. re-running the
whole task in order), skip this step's "fails" expectation — Steps 3-4 make this test pass
immediately, which is correct. This step's purpose is satisfied by Step 2's unit tests already
having failed first; this integration test's role is durability proof, not first-failure
discovery.

- [ ] **Step 8: Run integration test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_resolve_atomicity.py -v`

Expected: PASS.

- [ ] **Step 9: Run the full backend verification**

Run:
```bash
cd Backend && pytest
```

Expected: PASS — the full `Backend/tests/` root (mocked-DB tests), separate from
`tests/integration/` per this repo's testing gotcha about never combining test roots in one
invocation.

- [ ] **Step 10: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py Backend/app/services/ingestion_service.py Backend/tests/test_ingestion_service.py tests/integration/test_ingestion_resolve_atomicity.py
git commit -m "fix: make IngestionService.resolve() apply decisions independently

Each decision now commits before its file move runs, and a failure in
one decision no longer discards others already applied in the same
batch -- see
docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md."
```

---

## Task 2: API contract — `failed`/`move_failed` in `ResolveResponse`

**Files:**
- Modify: `Backend/app/api/ingestion.py:60-62`
- Modify: `shared/schemas/ingestionresolveresponse.schema.json`
- Modify (regenerated): `Frontend/memes-frontend/src/types/generated/all.d.ts`
- Modify: `backend_api.md`
- Modify: `Backend/tests/test_ingestion_endpoints.py`

**Interfaces:**
- Consumes: `IngestionService.resolve()`'s return shape from Task 1 (`rejected`/`kept`/`failed`/
  `move_failed`, with `failed` items shaped `{"image_id": str, "decision": str, "error": str}`
  and `move_failed` items shaped `{"image_id": str, "error": str}`).
- Produces: `IngestionResolveResponse` (frontend generated type) with the same four fields, for
  Task 3 to consume.

- [ ] **Step 1: Write the failing router test**

In `Backend/tests/test_ingestion_endpoints.py`, update the existing
`test_applies_reject_and_keep_decisions` (inside `class TestResolveCluster`) to match the real
service contract, and add a new test alongside it for the populated case:

```python
class TestResolveCluster:
    def test_applies_reject_and_keep_decisions(self, client, mock_service):
        reject_id = str(uuid.uuid4())
        keep_id = str(uuid.uuid4())
        mock_service.resolve.return_value = {
            "rejected": [reject_id], "kept": [keep_id], "failed": [], "move_failed": [],
        }

        response = client.post(
            "/api/ingestion/clusters/tier_a/resolve",
            json={"decisions": [
                {"image_id": reject_id, "decision": "reject"},
                {"image_id": keep_id, "decision": "keep"},
            ]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["rejected"] == [reject_id]
        assert data["kept"] == [keep_id]
        assert data["failed"] == []
        assert data["move_failed"] == []

    def test_returns_failed_and_move_failed_entries(self, client, mock_service):
        failed_id = str(uuid.uuid4())
        move_failed_id = str(uuid.uuid4())
        mock_service.resolve.return_value = {
            "rejected": [move_failed_id],
            "kept": [],
            "failed": [{"image_id": failed_id, "decision": "keep", "error": "db down"}],
            "move_failed": [{"image_id": move_failed_id, "error": "file locked"}],
        }

        response = client.post(
            "/api/ingestion/clusters/tier_a/resolve",
            json={"decisions": [
                {"image_id": failed_id, "decision": "keep"},
                {"image_id": move_failed_id, "decision": "reject"},
            ]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["failed"] == [{"image_id": failed_id, "decision": "keep", "error": "db down"}]
        assert data["move_failed"] == [{"image_id": move_failed_id, "error": "file locked"}]

    def test_rejects_unknown_decision_value(self, client, mock_service):
        response = client.post(
            "/api/ingestion/clusters/tier_a/resolve",
            json={"decisions": [{"image_id": str(uuid.uuid4()), "decision": "maybe"}]},
        )

        assert response.status_code == 422  # pydantic Literal validation, service never called
        mock_service.resolve.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py -v -k TestResolveCluster`

Expected: `test_applies_reject_and_keep_decisions` FAILS (`response.status_code == 200` fails --
the current `ResolveResponse` Pydantic model has no `failed`/`move_failed` fields, so FastAPI's
response validation rejects the mock's four-key dict... actually the model currently only
*allows* `rejected`/`kept`, so extra keys in the mock's return dict are silently dropped by
Pydantic, and the assertions on `data["failed"]`/`data["move_failed"]` fail with `KeyError`).
`test_returns_failed_and_move_failed_entries` FAILS the same way. `test_rejects_unknown_decision_value`
still PASSES (unaffected).

- [ ] **Step 3: Update the Pydantic response models**

In `Backend/app/api/ingestion.py`, replace the existing `ResolveResponse` class (lines 60-62)
with:

```python
class FailedDecision(BaseModel):
    image_id: str
    decision: str
    error: str


class MoveFailure(BaseModel):
    image_id: str
    error: str


class ResolveResponse(BaseModel):
    rejected: list[str]
    kept: list[str]
    failed: list[FailedDecision]
    move_failed: list[MoveFailure]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py -v`

Expected: PASS — full file.

- [ ] **Step 5: Update the shared JSON schema**

Replace the full content of `shared/schemas/ingestionresolveresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ingestionresolveresponse.schema.json",
  "title": "IngestionResolveResponse",
  "type": "object",
  "properties": {
    "rejected": { "type": "array", "items": { "type": "string" } },
    "kept": { "type": "array", "items": { "type": "string" } },
    "failed": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "image_id": { "type": "string" },
          "decision": { "type": "string" },
          "error": { "type": "string" }
        },
        "required": ["image_id", "decision", "error"]
      }
    },
    "move_failed": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "image_id": { "type": "string" },
          "error": { "type": "string" }
        },
        "required": ["image_id", "error"]
      }
    }
  },
  "required": ["rejected", "kept", "failed", "move_failed"]
}
```

- [ ] **Step 6: Regenerate the frontend types**

Run:
```bash
cd Frontend
bash generate-types.sh
```

Expected: exits cleanly with no error output. Then check the diff is scoped to the intended
type:

```bash
git diff Frontend/memes-frontend/src/types/generated/all.d.ts
```

Expected: the diff touches only the `IngestionResolveResponse` interface, adding `failed` and
`move_failed` array fields matching the schema above. No other generated interface should
change. If other interfaces show unrelated diffs, stop and investigate before continuing —
that would mean another schema file drifted out of sync with its generated type independently
of this change, which is out of scope to fix here.

- [ ] **Step 7: Update `backend_api.md`**

In `backend_api.md`, replace the `#### Resolve Cluster` section's example (the block currently
reading `// Response\n{ "rejected": ["a1b2..."], "kept": ["c3d4..."] }`):

```json
// Request
{ "decisions": [
  { "image_id": "a1b2...", "decision": "reject" },
  { "image_id": "c3d4...", "decision": "keep" }
] }

// Response
{
  "rejected": ["a1b2..."],
  "kept": ["c3d4..."],
  "failed": [],
  "move_failed": []
}
```

And add a sentence after the existing "Partial resolution is allowed" line, before the `- **URL**`
bullet:

```markdown
Each decision is applied independently -- one decision's failure (database or filesystem) never
discards others in the same call. `failed` lists decisions whose database write didn't apply
(safe to retry as-is); `move_failed` lists rejects whose database write committed but whose file
move failed (the image is correctly `rejected` in the database -- `undo_reject` on it is safe and
will simply find nothing to move back).
```

- [ ] **Step 8: Commit**

```bash
git add Backend/app/api/ingestion.py Backend/tests/test_ingestion_endpoints.py shared/schemas/ingestionresolveresponse.schema.json Frontend/memes-frontend/src/types/generated/all.d.ts backend_api.md
git commit -m "feat: add failed/move_failed to IngestionResolveResponse contract

Wires IngestionService.resolve()'s new per-decision result buckets
through the Pydantic response model, the shared JSON schema, the
regenerated frontend types, and backend_api.md -- see
docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md."
```

---

## Task 3: Frontend — consume `failed`/`move_failed`, clear by response not request

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts:35` (update the default mock return value)
- Test: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`

**Interfaces:**
- Consumes: `IngestionResolveResponse` from Task 2, now with `rejected: string[]`,
  `kept: string[]`, `failed: {image_id: string, decision: string, error: string}[]`,
  `move_failed: {image_id: string, error: string}[]`.
- Produces: no new exported interface — internal component behavior only.

- [ ] **Step 1: Update the default mock API return value**

In `Frontend/memes-frontend/src/test/mockApi.ts:35`, replace:

```typescript
    resolveIngestionCluster: vi.fn().mockResolvedValue({ rejected: [], kept: [] }),
```

with:

```typescript
    resolveIngestionCluster: vi.fn().mockResolvedValue({ rejected: [], kept: [], failed: [], move_failed: [] }),
```

This keeps every existing test that doesn't override `resolveIngestionCluster` passing against
the new required response shape.

- [ ] **Step 2: Update existing tests' mocked resolve responses**

`IngestionReviewPage.test.tsx` has several tests that call
`vi.fn().mockResolvedValue({ rejected: [...], kept: [...] })` for `resolveIngestionCluster`.
Update each to include the two new fields as empty arrays, matching Step 1's pattern. The
specific call sites (search for `mockResolvedValue({ rejected:` in the file) are:

- The `'submits a reject decision for the pending member and reloads'` test:
  `{ rejected: ['pending-1'], kept: [], failed: [], move_failed: [] }`
- The `'submits decisions for all clusters with a decision...'` test:
  `{ rejected: ['pending-1'], kept: ['pending-3'], failed: [], move_failed: [] }`
- The `'clears submitted decisions from state so a re-appearing image is not resubmitted'` test:
  `{ rejected: ['pending-1'], kept: [], failed: [], move_failed: [] }`

- [ ] **Step 3: Write the failing test for response-based clearing**

Add this test inside the `describe('submit all decisions', ...)` block, after the existing
`'clears submitted decisions from state so a re-appearing image is not resubmitted'` test (this
new test lives in the outer `describe('IngestionReviewPage', ...)` block instead, since it
covers per-cluster submit, not submit-all — place it after that same test, still inside the
outer block):

```typescript
  it('leaves a failed decision in local state for retry, and shows an error summary', async () => {
    // Regression test: once resolve() can partially succeed with a 200 response (see
    // docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md), clearing
    // decisions based on what was *sent* would silently discard ones that didn't actually
    // apply. Only ids present in the response's rejected/kept arrays should be cleared.
    const resolve = vi.fn().mockResolvedValue({
      rejected: [],
      kept: [],
      failed: [{ image_id: 'pending-1', decision: 'reject', error: 'db down' }],
      move_failed: [],
    })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster])
      .mockResolvedValueOnce([mockCluster]) // reload after submit -- cluster unchanged
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    await user.click(screen.getByText('Reject'))
    await user.click(screen.getByText('Submit decisions'))

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    // The decision failed to apply -- it must still be selected after reload, ready to retry,
    // and the page must surface that something needs attention.
    await waitFor(() => expect(screen.getByText('Reject')).toHaveClass('bg-red-600'))
    expect(screen.getByText(/1 decision\(s\) failed to apply/)).toBeInTheDocument()
  })

  it('shows a move-failed summary without treating it as a hard error', async () => {
    const resolve = vi.fn().mockResolvedValue({
      rejected: ['pending-1'],
      kept: [],
      failed: [],
      move_failed: [{ image_id: 'pending-1', error: 'file locked' }],
    })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster])
      .mockResolvedValueOnce([]) // resolved, drops out of the queue
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    await user.click(screen.getByText('Reject'))
    await user.click(screen.getByText('Submit decisions'))

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    await waitFor(() =>
      expect(screen.getByText(/were recorded but their file move failed/)).toBeInTheDocument()
    )
    // A move failure is not a hard error -- the page still shows the (now-empty) cluster list,
    // not the full-page error screen.
    expect(screen.getByText('No Tier A clusters need review right now.')).toBeInTheDocument()
  })
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx -t "leaves a failed decision in local state for retry"`

Expected: FAIL — `submitCluster` currently clears every id it *sent* (`clusterDecisions`)
unconditionally on a successful call, regardless of what the response reports, so `pending-1`'s
decision is cleared even though it's in `failed`, and no error message is shown.

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx -t "shows a move-failed summary"`

Expected: FAIL — no `move_failed` handling exists yet, so no matching error text renders.

- [ ] **Step 5: Implement the fix**

In `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`, replace the body of
`submitCluster` (from `setSubmitting(clusterIndex)` through the end of the function):

```typescript
    setSubmitting(clusterIndex)
    try {
      const response = await memesApi.resolveIngestionCluster(tier, clusterDecisions)
      setDecisions((prev) => {
        const next = { ...prev }
        for (const image_id of [...response.rejected, ...response.kept]) delete next[image_id]
        return next
      })
      if (response.failed.length > 0 || response.move_failed.length > 0) {
        setError(
          `${response.failed.length} decision(s) failed to apply and remain marked for retry` +
          (response.move_failed.length > 0
            ? `; ${response.move_failed.length} were recorded but their file move failed (safe to retry via undo/re-decide)`
            : "")
        )
      }
      load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to submit decisions")
    } finally {
      setSubmitting(null)
    }
```

And replace the body of `submitAll` (from `setSubmitting("all")` through the end of the
function) the same way:

```typescript
    setSubmitting("all")
    try {
      const response = await memesApi.resolveIngestionCluster(tier, allPendingDecisions)
      setDecisions((prev) => {
        const next = { ...prev }
        for (const image_id of [...response.rejected, ...response.kept]) delete next[image_id]
        return next
      })
      if (response.failed.length > 0 || response.move_failed.length > 0) {
        setError(
          `${response.failed.length} decision(s) failed to apply and remain marked for retry` +
          (response.move_failed.length > 0
            ? `; ${response.move_failed.length} were recorded but their file move failed (safe to retry via undo/re-decide)`
            : "")
        )
      }
      load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to submit all decisions")
    } finally {
      setSubmitting(null)
    }
```

The page's existing `if (error) return (...)` branch (around line 219) currently renders a
full-page error screen instead of the normal cluster view. A `failed`/`move_failed` summary must
not trigger that full-page takeover -- it's a transient, actionable notice, not a load failure.
Move the error banner so it renders inline above the cluster list instead of replacing the page.
Change the top-level `if (error) return (...)` block to only apply when there's no `status` yet
(i.e. keep today's full-page behavior for genuine load failures, which have `status === null`),
and render a dismissable-by-reload inline banner otherwise. Replace:

```typescript
  if (error) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-red-500 mb-3">{error}</p>
      <button
        className="text-sm rounded bg-blue-600 text-white px-3 py-1"
        onClick={() => { setLoading(true); load() }}
      >
        Retry
      </button>
    </div>
  )

  if (!status) return (
```

with:

```typescript
  if (error && !status) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-red-500 mb-3">{error}</p>
      <button
        className="text-sm rounded bg-blue-600 text-white px-3 py-1"
        onClick={() => { setLoading(true); load() }}
      >
        Retry
      </button>
    </div>
  )

  if (!status) return (
```

Then add the inline banner into the main return block, directly after `<StatusBanner status={status} />`:

```typescript
      <StatusBanner status={status} />

      {error && (
        <p className="text-sm text-red-500 mb-3">{error}</p>
      )}
```

Note: `load()`'s existing `.catch` handler already calls `setStatus(null)` on any failure
(`IngestionReviewPage.tsx:121`), whether it's the first load or a later reload. So a genuine
`load()` failure always clears `status` back to `null` and correctly falls back to the full-page
error screen via the `error && !status` branch above, regardless of when it happens. No further
change is needed here — `status` and `error` already move together on a genuine load failure.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`

Expected: PASS — full file.

- [ ] **Step 7: Run the full frontend verification suite**

Run:
```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
vitest run
```

Expected: `tsc -b` clean (confirms `IngestionResolveResponse`'s new fields type-check against
Task 2's regenerated `all.d.ts`), `eslint src/` clean (0 warnings), all tests pass.

- [ ] **Step 8: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/test/mockApi.ts Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "fix: clear ingestion decisions by response, not request

resolve() can now partially succeed -- only ids the backend actually
confirmed (rejected/kept) are cleared from local state, and a
failed/move_failed summary surfaces inline without replacing the page
-- see
docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md."
```
