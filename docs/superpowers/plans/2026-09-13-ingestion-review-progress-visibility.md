# Ingestion Review Progress Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the frozen intake `stats` banner with live, tier-scoped review-progress numbers, refreshed after every submit (not just when the visible queue empties), plus session-relative deltas.

**Architecture:** Two new repository count queries reuse the existing per-tier subject-set predicates (`get_tier_candidate_rows`'s shape) to compute "how many pending images still need review" and "how many are blocked batch-wide" without materializing rows. `IngestionService.get_run_status` gains two new nullable fields built from those counts. The frontend adds a light status-only refetch after every submit (the current code only reloads when the queue empties) and two client-side deltas computed from the fields already there — no new endpoint.

**Tech Stack:** FastAPI + SQLAlchemy async raw SQL, React 19 + TS, hand-written JSON Schema → generated TS/Kotlin/Python DTOs, Vitest + pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md`

## Global Constraints

- New repository methods take `(batch_id, tier: str, distance_low: float, distance_high: float)` and assert `tier in ("tier_a", "tier_b")` before using it to select a column name — never derived from external input.
- `tier_remaining`/`blocked_total` on `IngestionRunStatus`/`RunStatusResponse` are both `int | None`, both `None` together outside `tier_a_review`/`tier_b_review`/`promoted` stages, both real integers together inside them.
- `blocked_total` is the union of both tiers' precise pending-only subject sets — never derived from `get_blocked_pending_ids` (that method can include a non-pending id for a `cross_corpus` match; reusing it would quietly overcount a user-facing number).
- No change to `resolve`, `mark_reviewed`, `reject_image`, the tier bands, or the cursor. No new endpoint — `GET /api/ingestion/run` returns richer data on the same route.
- `cd Backend && pytest` on its own. Integration: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the CLI, never combined with `Backend/tests/`.
- Generated files regenerated across all THREE trees (TS, Kotlin, Python DTOs) and committed with the schema change.
- Frontend gate: `tsc -b`, `eslint src/` (0 warnings), `vitest run`.

---

## File Structure

**Backend — modify:** `Backend/app/repositories/ingestion_repository.py` (2 new methods), `Backend/app/services/ingestion_service.py` (`_tier_for_stage` + `get_run_status`), `Backend/app/api/ingestion.py` (`RunStatusResponse` fields), `Backend/tests/test_ingestion_service.py`, `Backend/tests/test_ingestion_endpoints.py`, `backend_api.md`.
**Backend — create:** `tests/integration/test_ingestion_review_progress.py`.
**Shared — modify:** `shared/schemas/ingestionrunstatus.schema.json`.
**Generated (regenerated, not hand-edited) — all THREE trees:** `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/**/Models.kt`, `Backend/app/types/generated/ingestionrunstatus.py` (or wherever datamodel-codegen places it).
**Frontend — modify:** `src/pages/IngestionReviewPage.tsx`, `src/pages/IngestionReviewPage.test.tsx`.

---

## Task 1: Repository — `count_unreviewed_subjects` + `unreviewed_subject_ids`

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py`
- Create: `tests/integration/test_ingestion_review_progress.py`

**Interfaces:**
- Produces: `IngestionRepository.count_unreviewed_subjects(batch_id, tier: str, distance_low: float, distance_high: float) -> int`
- Produces: `IngestionRepository.unreviewed_subject_ids(batch_id, tier: str, distance_low: float, distance_high: float) -> set`

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_ingestion_review_progress.py`:

```python
"""Integration tests for the review-progress count queries. Live PostgreSQL (see conftest)."""
import uuid
from datetime import datetime, timezone

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates

TIER_A_LOW, TIER_A_HIGH = 0.0, 0.05
TIER_B_LOW, TIER_B_HIGH = 0.05, 0.30


async def _run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _img(session, status, batch_id):
    i = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(i); await session.flush(); return i.id


async def _pair(session, a, b, d, tier_b_reviewed=False):
    row = TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=d, match_source="in_batch")
    if tier_b_reviewed:
        row.tier_b_reviewed_at = datetime.now(timezone.utc)
    session.add(row); await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_count_unreviewed_subjects_excludes_reviewed_and_rejected(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    rejected = await _img(db_session, "rejected", bid)
    await _pair(db_session, p1, p2, 0.10)                        # open -> both count
    await _pair(db_session, p3, rejected, 0.12)                  # other side rejected -> excluded
    already_reviewed_a, already_reviewed_b = p1, p2
    await _pair(db_session, already_reviewed_a, already_reviewed_b, 0.11, tier_b_reviewed=True)  # a 2nd, already-reviewed pair between the same two -- p1/p2 still open via the first pair

    repo = IngestionRepository(db_session)
    n = await repo.count_unreviewed_subjects(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH)
    assert n == 2  # p1, p2 -- p3 excluded (only pair has a rejected other side)


@pytest.mark.asyncio(loop_scope="session")
async def test_count_unreviewed_subjects_respects_band_and_tier(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)  # tier A band, not tier B

    repo = IngestionRepository(db_session)
    assert await repo.count_unreviewed_subjects(bid, "tier_a", TIER_A_LOW, TIER_A_HIGH) == 2
    assert await repo.count_unreviewed_subjects(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_unreviewed_subject_ids_returns_the_actual_ids(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    other = await _img(db_session, "active", bid)
    await _pair(db_session, p1, p2, 0.10)
    await _pair(db_session, p2, other, 0.15)

    repo = IngestionRepository(db_session)
    ids = await repo.unreviewed_subject_ids(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH)
    assert ids == {p1, p2}  # `other` is active, never a subject


@pytest.mark.asyncio(loop_scope="session")
async def test_unreviewed_subject_ids_union_dedupes_a_subject_open_in_both_tiers(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.02)   # tier A band
    await _pair(db_session, p1, p3, 0.10)   # tier B band -- p1 open in BOTH tiers

    repo = IngestionRepository(db_session)
    ids_a = await repo.unreviewed_subject_ids(bid, "tier_a", TIER_A_LOW, TIER_A_HIGH)
    ids_b = await repo.unreviewed_subject_ids(bid, "tier_b", TIER_B_LOW, TIER_B_HIGH)
    union = ids_a | ids_b
    assert union == {p1, p2, p3}   # p1 counted once despite being in both sets
    assert len(union) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_review_progress.py -v`
Expected: FAIL — `count_unreviewed_subjects`/`unreviewed_subject_ids` not defined.

- [ ] **Step 3: Implement**

Add to `IngestionRepository` (near `get_tier_candidate_rows`):

```python
    async def count_unreviewed_subjects(self, batch_id, tier: str, distance_low: float, distance_high: float) -> int:
        """Count of distinct PENDING images in this batch with at least one still-open (not yet
        reviewed for this tier, other side not rejected) candidate pair in this tier's band -- the
        same subject set get_tier_candidate_rows/list_tier_b_review_page build, just
        COUNT(DISTINCT ...) instead of materializing rows. See
        docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md."""
        assert tier in ("tier_a", "tier_b"), f"unknown tier: {tier!r}"
        reviewed_col = "tier_a_reviewed_at" if tier == "tier_a" else "tier_b_reviewed_at"
        # reviewed_col is one of exactly two hardcoded column names (asserted above), never
        # derived from external input -- the f-string only ever selects between two fixed literals.
        sql = text(f"""
            SELECT count(DISTINCT subject_id) FROM (
                SELECT td.image_id1 AS subject_id
                FROM tmp_duplicates td
                JOIN images s ON s.id = td.image_id1
                JOIN images o ON o.id = td.image_id2
                WHERE td.{reviewed_col} IS NULL
                  AND td.distance >= :low AND td.distance < :high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
                  AND o.status <> 'rejected'
                UNION ALL
                SELECT td.image_id2 AS subject_id
                FROM tmp_duplicates td
                JOIN images s ON s.id = td.image_id2
                JOIN images o ON o.id = td.image_id1
                WHERE td.{reviewed_col} IS NULL
                  AND td.distance >= :low AND td.distance < :high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
                  AND o.status <> 'rejected'
            ) subjects
        """)
        return (await self.session.execute(
            sql, {"low": distance_low, "high": distance_high, "batch_id": batch_id})).scalar()

    async def unreviewed_subject_ids(self, batch_id, tier: str, distance_low: float, distance_high: float) -> set:
        """Same subject set as count_unreviewed_subjects, as ids rather than a count -- used to
        union across tiers for a batch-wide blocked-total that can't just sum two per-tier counts
        (an image can be open in both tiers' bands at once)."""
        assert tier in ("tier_a", "tier_b"), f"unknown tier: {tier!r}"
        reviewed_col = "tier_a_reviewed_at" if tier == "tier_a" else "tier_b_reviewed_at"
        sql = text(f"""
            SELECT DISTINCT subject_id FROM (
                SELECT td.image_id1 AS subject_id
                FROM tmp_duplicates td
                JOIN images s ON s.id = td.image_id1
                JOIN images o ON o.id = td.image_id2
                WHERE td.{reviewed_col} IS NULL
                  AND td.distance >= :low AND td.distance < :high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
                  AND o.status <> 'rejected'
                UNION ALL
                SELECT td.image_id2 AS subject_id
                FROM tmp_duplicates td
                JOIN images s ON s.id = td.image_id2
                JOIN images o ON o.id = td.image_id1
                WHERE td.{reviewed_col} IS NULL
                  AND td.distance >= :low AND td.distance < :high
                  AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
                  AND o.status <> 'rejected'
            ) subjects
        """)
        rows = (await self.session.execute(
            sql, {"low": distance_low, "high": distance_high, "batch_id": batch_id})).all()
        return {r.subject_id for r in rows}
```

- [ ] **Step 4: Run to verify pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_review_progress.py -v`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py tests/integration/test_ingestion_review_progress.py
git commit -m "feat: repo count queries for ingestion review progress

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 2: Service — `_tier_for_stage` + `get_run_status`

**Files:**
- Modify: `Backend/app/services/ingestion_service.py`
- Modify: `Backend/tests/test_ingestion_service.py`

**Interfaces:**
- Consumes: `IngestionRepository.count_unreviewed_subjects(...)`, `IngestionRepository.unreviewed_subject_ids(...)` (Task 1); `_tier_band(tier)` (existing).
- Produces: `_tier_for_stage(stage: Optional[str]) -> Optional[str]` (module-level function).
- Produces: `IngestionService.get_run_status(batch_id=None) -> dict` — same keys as before plus `tier_remaining: int | None`, `blocked_total: int | None`.

- [ ] **Step 1: Write the failing unit tests**

Find the existing `class TestGetRunStatus` in `Backend/tests/test_ingestion_service.py` if one exists; if not, add this class (check the file first — do not duplicate an existing class of this name):

```python
class TestGetRunStatus:
    async def test_no_tier_stage_returns_none_progress(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="hash_dedup", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] is None
        assert status["blocked_total"] is None
        mock_repo.count_unreviewed_subjects.assert_not_called()
        mock_repo.unreviewed_subject_ids.assert_not_called()

    async def test_tier_a_review_computes_tier_a_remaining(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="tier_a_review", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run
        mock_repo.count_unreviewed_subjects.return_value = 7
        mock_repo.unreviewed_subject_ids.return_value = set()

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] == 7
        mock_repo.count_unreviewed_subjects.assert_awaited_once()
        tier_arg = mock_repo.count_unreviewed_subjects.call_args.args[1]
        assert tier_arg == "tier_a"

    async def test_blocked_total_unions_both_tiers(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="tier_b_review", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run
        mock_repo.count_unreviewed_subjects.return_value = 3
        s1, s2, s3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.unreviewed_subject_ids.side_effect = [{s1, s2}, {s2, s3}]  # tier_a call, then tier_b call

        status = await service.get_run_status(run.run_id)

        assert status["blocked_total"] == 3  # {s1, s2, s3} -- s2 not double-counted
        assert mock_repo.unreviewed_subject_ids.await_count == 2

    async def test_promoted_stage_still_computes_tier_b_remaining(self, service, mock_repo):
        import uuid
        from types import SimpleNamespace
        run = SimpleNamespace(run_id=uuid.uuid4(), status="started", stage="promoted", stats={},
                              created_at="t", completed_at=None)
        mock_repo.get_run.return_value = run
        mock_repo.count_unreviewed_subjects.return_value = 0
        mock_repo.unreviewed_subject_ids.return_value = set()

        status = await service.get_run_status(run.run_id)

        assert status["tier_remaining"] == 0
        tier_arg = mock_repo.count_unreviewed_subjects.call_args.args[1]
        assert tier_arg == "tier_b"
```

Import `Optional` if not already imported at the top of the test file (check first).

- [ ] **Step 2: Run to verify failure**

Run: `cd Backend && pytest tests/test_ingestion_service.py::TestGetRunStatus -v`
Expected: FAIL — `KeyError: 'tier_remaining'` (the dict returned by `get_run_status` doesn't have the key yet).

- [ ] **Step 3: Implement**

In `Backend/app/services/ingestion_service.py`, add a module-level function near `_tier_band` (not a method — it doesn't need `self`):

```python
def _tier_for_stage(stage: Optional[str]) -> Optional[str]:
    """Mirrors IngestionReviewPage.tsx's tierForStage exactly -- keep both in sync if either
    changes. 'promoted' falls back to tier_b's (by-then-empty) queue for the same reason the
    frontend does: once a run completes it drops out of get_run_status entirely, so this is a
    safety net, not the normal path."""
    if stage == "tier_a_review":
        return "tier_a"
    if stage in ("tier_b_review", "promoted"):
        return "tier_b"
    return None
```

Replace the existing `get_run_status` method body:

```python
    async def get_run_status(self, batch_id: Optional[UUID] = None) -> dict:
        resolved_id = await self._resolve_batch_id(batch_id)
        run = await self.repo.get_run(resolved_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Ingestion run not found")

        tier_remaining = None
        blocked_total = None
        current_tier = _tier_for_stage(run.stage)
        if current_tier is not None:
            low, high = _tier_band(current_tier)
            tier_remaining = await self.repo.count_unreviewed_subjects(resolved_id, current_tier, low, high)
            tier_a_low, tier_a_high = _tier_band("tier_a")
            tier_b_low, tier_b_high = _tier_band("tier_b")
            blocked_ids = await self.repo.unreviewed_subject_ids(resolved_id, "tier_a", tier_a_low, tier_a_high)
            blocked_ids |= await self.repo.unreviewed_subject_ids(resolved_id, "tier_b", tier_b_low, tier_b_high)
            blocked_total = len(blocked_ids)

        return {
            "run_id": str(run.run_id),
            "status": run.status,
            "stage": run.stage,
            "stats": run.stats,
            "tier_remaining": tier_remaining,
            "blocked_total": blocked_total,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`
Expected: PASS — `TestGetRunStatus` plus every other existing service test.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/services/ingestion_service.py Backend/tests/test_ingestion_service.py
git commit -m "feat: IngestionService.get_run_status computes live review-progress counts

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 3: API — schema, models, all THREE generated trees, docs

**Files:**
- Modify: `Backend/app/api/ingestion.py`
- Modify: `shared/schemas/ingestionrunstatus.schema.json`
- Modify: `Backend/tests/test_ingestion_endpoints.py`
- Modify: `backend_api.md`
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx` (the one base `runStatus` fixture — everything else in that file spreads from it)
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, Android `Models.kt`, `Backend/app/types/generated/`

**Interfaces:**
- Consumes: `IngestionService.get_run_status(...)` (Task 2) — dict now has `tier_remaining`/`blocked_total`.
- Produces: `GET /api/ingestion/run` → `RunStatusResponse` with the 2 new fields. Generated TS/Kotlin/Python `IngestionRunStatus`/equivalent gains `tier_remaining: number | null`, `blocked_total: number | null`.

- [ ] **Step 1: Write the failing endpoint test, and fix the existing one's fixture**

There's already a `TestGetRunStatus` class in `Backend/tests/test_ingestion_endpoints.py` with one test,
`test_returns_run_status`, whose `mock_service.get_run_status.return_value` dict does NOT include
`tier_remaining`/`blocked_total`. Once Step 3 makes those fields required-but-nullable on
`RunStatusResponse`, that existing test's mocked dict is missing required keys and will start failing
Pydantic response validation (a 500, not the assertions it currently has) unless fixed. Update its dict in
the same edit:

```python
class TestGetRunStatus:
    def test_returns_run_status(self, client, mock_service):
        mock_service.get_run_status.return_value = {
            "run_id": str(uuid.uuid4()), "status": "started", "stage": "tier_a_review",
            "stats": {"intake": 3}, "tier_remaining": None, "blocked_total": None,
            "created_at": datetime.now(timezone.utc), "completed_at": None,
        }

        response = client.get("/api/ingestion/run")

        assert response.status_code == 200
        assert response.json()["status"] == "started"
        assert response.json()["stage"] == "tier_a_review"

    def test_returns_review_progress_fields(self, client, mock_service):
        mock_service.get_run_status.return_value = {
            "run_id": str(uuid.uuid4()), "status": "started", "stage": "tier_b_review",
            "stats": {}, "tier_remaining": 12, "blocked_total": 20,
            "created_at": datetime.now(timezone.utc), "completed_at": None,
        }

        response = client.get("/api/ingestion/run")

        assert response.status_code == 200
        body = response.json()
        assert body["tier_remaining"] == 12
        assert body["blocked_total"] == 20
```

(Only add the `"tier_remaining": None, "blocked_total": None` keys to the existing test's dict — don't
otherwise change its assertions. Add the new `test_returns_review_progress_fields` method alongside it in
the same class.)

- [ ] **Step 2: Run to verify failure**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py::TestGetRunStatus -v`
Expected: `test_returns_run_status` still PASSES (its dict was fixed in the same edit — it was never meant
to fail); `test_returns_review_progress_fields` FAILS — `RunStatusResponse` rejects/drops the extra keys,
or the response body is missing them (Pydantic strips unknown-to-the-model fields silently by default, so
the assertion on `body["tier_remaining"]` is what actually fails: `KeyError`).

- [ ] **Step 3: Update the Pydantic model**

In `Backend/app/api/ingestion.py`, `RunStatusResponse`:

```python
class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    stage: Optional[str]
    stats: Optional[dict]
    tier_remaining: Optional[int]
    blocked_total: Optional[int]
    created_at: datetime
    completed_at: Optional[datetime]
```

- [ ] **Step 4: Update the shared schema**

In `shared/schemas/ingestionrunstatus.schema.json`, add to `properties` (after `stats`, before `created_at` — matching the Pydantic field order above) and to `required`:

```json
    "tier_remaining": { "type": ["integer", "null"], "description": "Pending images still needing review in the CURRENT stage's tier; null when the stage has no active tier (hash_dedup, ocr_prepass)." },
    "blocked_total": { "type": ["integer", "null"], "description": "Pending images in this batch with an unresolved candidate pair in either tier; null under the same condition as tier_remaining." }
```

`required` becomes `["run_id", "status", "stage", "stats", "tier_remaining", "blocked_total", "created_at", "completed_at"]`.

- [ ] **Step 5: Run endpoint test to pass**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py -v`
Expected: PASS.

- [ ] **Step 6: Regenerate types — all THREE generators off `all.schema.json`**

```bash
cd Frontend && bash generate-types.sh && cd ..
python AndroidClient/scripts/generate_dtos.py
grep -n "tier_remaining" Frontend/memes-frontend/src/types/generated/all.d.ts   # exists on IngestionRunStatus
```

Then the Python DTO tree (`Backend/app/types/generated/`), per `documents/generation.md`'s documented `datamodel-codegen` command, run from `Backend/`. Expect `ingestionrunstatus.py` (or wherever it lives) to gain the two fields. The generator also rewrites the `timestamp:` header line in every other already-generated file it touches on each run — revert those (`git checkout --`) so the commit contains only real content changes; verify with `git diff --stat` before staging. `cd Backend && pytest -q` must still pass after.

- [ ] **Step 7: Fix the frontend fixture that's now missing required fields**

`Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx` — the base `runStatus` object (everything else in the file spreads `{ ...runStatus, ... }`, so this one edit fixes every derived variant):

```ts
const runStatus: IngestionRunStatus = {
  run_id: 'r1', status: 'started', stage: 'tier_a_review', stats: {}, tier_remaining: null, blocked_total: null,
  created_at: '', completed_at: null,
}
```

Run: `cd Frontend/memes-frontend && npx tsc -b` — must be clean (this is purely fixing a now-missing-required-field compile error; no other frontend behavior changes in this task).

- [ ] **Step 8: `backend_api.md`**

Find the existing `GET /api/ingestion/run` section and add: *"`tier_remaining` (int, nullable) and `blocked_total` (int, nullable): live review-progress counts, computed on every call. Both `null` outside `tier_a_review`/`tier_b_review`/`promoted` stages (no active tier to count); both real integers inside them. `tier_remaining` is scoped to whichever tier `stage` currently is; `blocked_total` is batch-wide across both tiers."*

- [ ] **Step 9: Backend regression + commit**

Run: `cd Backend && pytest -q` — PASS.

```bash
git add Backend/app/api/ingestion.py shared/schemas/ingestionrunstatus.schema.json \
        Backend/tests/test_ingestion_endpoints.py backend_api.md \
        Frontend/memes-frontend/src/types/generated/all.d.ts \
        Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx \
        Backend/app/types/generated/ AndroidClient/
git commit -m "feat: expose tier_remaining/blocked_total on GET /api/ingestion/run

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

---

## Task 4: Frontend — refetch after every submit, banner, session-relative deltas

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`

**Interfaces:**
- Consumes: `IngestionRunStatus.tier_remaining`/`.blocked_total` (Task 3), `memesApi.getIngestionRunStatus()` (existing, unchanged signature).
- Produces: no new exports; the finished page.

- [ ] **Step 1: Write the failing page tests**

Add to `IngestionReviewPage.test.tsx` (a new `describe` block; reuse the existing `runStatus`/`tbItem`/`tbPage` helpers already in the file):

```tsx
describe('IngestionReviewPage — progress visibility', () => {
  it('shows tier_remaining and blocked_total in the banner when present', async () => {
    const status = { ...runStatus, stage: 'tier_a_review', tier_remaining: 5, blocked_total: 9 }
    const api = makeMockApi({ getIngestionRunStatus: vi.fn().mockResolvedValue(status) })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText(/5/)
    expect(screen.getByText(/still need/i)).toBeInTheDocument()
    expect(screen.getByText(/9/)).toBeInTheDocument()
  })

  it('does not show progress numbers when tier_remaining is null', async () => {
    const status = { ...runStatus, stage: 'ocr_prepass', tier_remaining: null, blocked_total: null }
    const api = makeMockApi({ getIngestionRunStatus: vi.fn().mockResolvedValue(status) })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText(/Candidates haven't been computed/i)
    expect(screen.queryByText(/still need/i)).toBeNull()
  })

  it('refreshes status after a partial submit that leaves units visible, without a full reload', async () => {
    const first = { ...runStatus, stage: 'tier_b_review', tier_remaining: 5, blocked_total: 5 }
    const second = { ...first, tier_remaining: 4, blocked_total: 4 }
    const getIngestionRunStatus = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    const getIngestionTierBReview = vi.fn().mockResolvedValue(
      tbPage([tbItem('s1', [['c1', 'active']]), tbItem('s2', [['c2', 'active']])]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['s1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({ getIngestionRunStatus, getIngestionTierBReview, resolveIngestionCluster })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await waitFor(() => expect(getIngestionRunStatus).toHaveBeenCalledTimes(2))
    expect(getIngestionTierBReview).toHaveBeenCalledTimes(1)  // no full reload -- s2's card is still visible
    expect(screen.getByText('s2.jpg')).toBeInTheDocument()
    await screen.findByText(/4/)  // banner reflects the refreshed count
  })

  it('shows a delta message after a submit that reduced tier_remaining', async () => {
    const first = { ...runStatus, stage: 'tier_b_review', tier_remaining: 5, blocked_total: 5 }
    const second = { ...first, tier_remaining: 3, blocked_total: 3 }
    const getIngestionRunStatus = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    const getIngestionTierBReview = vi.fn().mockResolvedValue(
      tbPage([tbItem('s1', [['c1', 'active']]), tbItem('s2', [['c2', 'active']])]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['s1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({ getIngestionRunStatus, getIngestionTierBReview, resolveIngestionCluster })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await screen.findByText(/fewer image.*remaining/i)
  })

  it('a failed progress refresh after a partial submit does not surface a page error', async () => {
    const first = { ...runStatus, stage: 'tier_b_review', tier_remaining: 5, blocked_total: 5 }
    const getIngestionRunStatus = vi.fn().mockResolvedValueOnce(first).mockRejectedValueOnce(new Error('boom'))
    const getIngestionTierBReview = vi.fn().mockResolvedValue(
      tbPage([tbItem('s1', [['c1', 'active']]), tbItem('s2', [['c2', 'active']])]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['s1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({ getIngestionRunStatus, getIngestionTierBReview, resolveIngestionCluster })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await waitFor(() => expect(getIngestionRunStatus).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('boom')).toBeNull()
    expect(screen.getByText('s2.jpg')).toBeInTheDocument()  // submit's own success path unaffected
  })
})
```

Add `waitFor` to the existing `@testing-library/react` import at the top of the file if it isn't already imported (check first).

- [ ] **Step 2: Run to verify failure**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: the new `describe` block fails (banner doesn't render the numbers yet; no second `getIngestionRunStatus` call on partial submit).

- [ ] **Step 3: Add the baseline ref and capture it in `load()`**

Near the other `useRef` declarations (`confirmAllTimeoutRef`, `loadingMoreRef`, around line 183-184):

```tsx
  // Captured once, the first time `load()` sees a non-null tier_remaining -- the "since you
  // opened this page" baseline. A ref (not state) because it must NOT trigger a re-render or
  // reset on every load() call, only ever be set the first time.
  const progressBaselineRef = useRef<{ tierRemaining: number; blockedTotal: number } | null>(null)
```

In `load()`, right after `setStatus(s)` (around line 192):

```tsx
      const s = await memesApi.getIngestionRunStatus()
      setStatus(s)
      if (s && s.tier_remaining !== null && progressBaselineRef.current === null) {
        progressBaselineRef.current = { tierRemaining: s.tier_remaining, blockedTotal: s.blocked_total ?? 0 }
      }
      setError(null)
```

- [ ] **Step 4: Refetch status after a partial submit; compute the delta message**

In `runSubmit`, the block around lines 404-413:

```tsx
      // Ruling 3: reload only when the on-screen queue has fully emptied -- restores the old
      // auto-advance (Tier A -> Tier B, via load() also refetching run status) and, when more
      // pages exist, pulls the next unreviewed page-1 work in place of a bare "Load more" button.
      // Every submit that leaves units visible stays purely optimistic (no reload/scroll jump).
      let submitDeltaMessage: string | null = null
      if (finalCount === 0) {
        await load()
      } else {
        // The visible queue didn't empty, so a full reload isn't warranted -- but the progress
        // numbers (tier_remaining/blocked_total) did change. Refresh just the status, not the
        // whole page, so the banner reflects this submit instead of going stale until the queue
        // happens to empty.
        try {
          const prevRemaining = status?.tier_remaining ?? null
          const freshStatus = await memesApi.getIngestionRunStatus()
          setStatus(freshStatus)
          if (prevRemaining !== null && freshStatus?.tier_remaining != null) {
            const delta = prevRemaining - freshStatus.tier_remaining
            if (delta > 0) {
              submitDeltaMessage = `${delta} fewer image${delta === 1 ? "" : "s"} remaining in ${tier === "tier_b" ? TIER_LABEL.tier_b : TIER_LABEL.tier_a} after that submit`
            }
          }
        } catch {
          // Best-effort -- a failed progress refresh shouldn't surface as a page error or block
          // a submit that already succeeded.
        }
      }
      // Set the summary after any reload -- load()'s success path clears `error`, so setting it
      // first would have the reload immediately wipe a move-failed / partial-failure summary.
      const resolveSummary = formatResolveSummary(response)
      setError([submitDeltaMessage, resolveSummary].filter(Boolean).join("; ") || null)
```

(This replaces the existing `if (finalCount === 0) { await load() }` block AND the `setError(formatResolveSummary(response))` line right after it — both become the single block above.)

- [ ] **Step 5: Update `StatusBanner`**

Replace the `StatusBanner` function (lines 141-159) and its call site (line 472):

```tsx
function StatusBanner({ status, tier, baseline }: {
  status: IngestionRunStatus | null
  tier: IngestionTier | null
  baseline: { tierRemaining: number; blockedTotal: number } | null
}) {
  if (!status) return null
  const stats = status.stats ?? {}
  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <div className="flex items-center gap-3">
        <span className="text-sm text-gray-500">Run</span>
        <span className="font-mono text-xs text-gray-700">{status.run_id}</span>
        <span className="text-sm text-gray-500 ml-4">Stage</span>
        <span className="font-semibold">{status.stage}</span>
      </div>
      {status.tier_remaining !== null && (
        <div className="mt-2 text-sm text-gray-700">
          <span className="font-semibold">{status.tier_remaining}</span>
          {" "}image{status.tier_remaining === 1 ? "" : "s"} still need{status.tier_remaining === 1 ? "s" : ""}{" "}
          {tier ? TIER_LABEL[tier] : ""} review
          {status.blocked_total !== null && (
            <span className="text-gray-500"> · {status.blocked_total} blocked from promotion</span>
          )}
          {baseline && (
            <span className="text-gray-400 ml-2">
              (-{Math.max(0, baseline.tierRemaining - status.tier_remaining)} since you opened this page)
            </span>
          )}
        </div>
      )}
      <div className="mt-2 flex gap-4 text-xs text-gray-400">
        {Object.entries(stats).map(([key, value]) => (
          <span key={key}>{key}: <span className="font-semibold">{String(value)}</span></span>
        ))}
      </div>
    </div>
  )
}
```

Call site (was `<StatusBanner status={status} />`):

```tsx
      <StatusBanner status={status} tier={tier} baseline={progressBaselineRef.current} />
```

- [ ] **Step 6: Run the page tests**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: PASS — existing tests unchanged and still green, the new progress-visibility block green.

- [ ] **Step 7: Full frontend gate**

```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/ && npx vitest run
```
Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "feat: live review-progress numbers + session-relative deltas in the banner

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

- [ ] **Step 9: Full backend + integration sweep + spec status**

```bash
cd Backend && pytest -q
```
Expected: PASS.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q
```
Expected: PASS, full unfiltered root (catches any cross-file pollution, not just the ingestion-scoped slice).

`docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md`: `status: approved` → `status: done`; add `Plan: docs/superpowers/plans/2026-09-13-ingestion-review-progress-visibility.md` under the status line.

```bash
git add docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md
git commit -m "docs: mark ingestion review progress visibility spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YJ6xy6GPd2tDsG9MUgiQuj"
```

- [ ] **Step 10: Whole-branch review**

Invoke `superpowers:requesting-code-review` for the branch against the spec. One iteration; fix action points; post the final summary. Pay attention to: the two new raw-SQL methods' injection surface (all values bound params; `reviewed_col`'s f-string interpolation is asserted to one of two fixed literals only), whether `blocked_total`'s union logic is genuinely double-count-free, and whether the "since last submit" delta can ever show a stale/wrong number if `getIngestionRunStatus` races with something else touching `status`.

---

## Self-Review (completed during planning)

**Spec coverage:**
- Design §1 (repository count queries) → Task 1.
- Design §2 (service `_tier_for_stage` + `get_run_status`) → Task 2.
- Design §3 (schema + all 3 generator trees) → Task 3.
- Design §4 (refetch after every submit) → Task 4 Step 4.
- Design §5 (session-relative deltas, banner) → Task 4 Steps 3 and 5.
- Key facts: `get_blocked_pending_ids`'s imprecision (why `blocked_total` doesn't reuse it) → Task 1's
  `unreviewed_subject_ids` design, explicitly not calling that method anywhere in this plan.
- Non-goals respected: no change to `resolve`/`mark_reviewed`/tier bands/cursor anywhere in this plan; no
  new endpoint (`GET /api/ingestion/run` is the same route throughout); the intake `stats` dict is kept in
  the banner (demoted to a smaller line), not removed.

**Placeholder scan:** none. Every code step has literal code; every test step names the command + expected
result.

**Type consistency:** `count_unreviewed_subjects(batch_id, tier, distance_low, distance_high) -> int` and
`unreviewed_subject_ids(batch_id, tier, distance_low, distance_high) -> set` — identical signatures used
consistently in Task 1's implementation, Task 1's tests, and Task 2's service calls and mocked-repo tests.
`_tier_for_stage` return values (`"tier_a"`/`"tier_b"`/`None`) match exactly what `_tier_band` and the two
repo methods expect for `tier`. `tier_remaining`/`blocked_total` field names identical across the service
dict (Task 2), the Pydantic model and JSON schema (Task 3), the generated TS type, and the frontend's
`status.tier_remaining`/`status.blocked_total` accesses (Task 4) — no renaming anywhere in the chain.
`progressBaselineRef`'s shape (`{ tierRemaining, blockedTotal }`) is defined once in Task 4 Step 3 and
consumed identically in Step 5's `StatusBanner` props.
