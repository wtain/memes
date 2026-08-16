# Ingestion Decision Staleness Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a Keep/Reject decision that was set on an image but never submitted from later being silently applied in a review context (tier, or member status) the reviewer never actually acted on.

**Architecture:** Three independent layers, from cheapest/most-certain to last-resort: (1) the frontend clears its local `decisions` map whenever the review `tier` changes, (2) the frontend re-checks each member's *current* status at submit time rather than trusting a decision set earlier, (3) the backend repository refuses to reject an image that isn't currently `pending`, regardless of what the frontend sends.

**Tech Stack:** FastAPI + SQLAlchemy async (Backend/app/repositories/ingestion_repository.py), React + TypeScript (Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx), pytest (backend), vitest + @testing-library/react (frontend).

## Global Constraints

- `mark_reviewed()` (the Keep path) stays unguarded — it never touches `Image.status`, so a stale Keep is harmless. Do not add a status guard to it. See the spec's "Non-goals" section for why.
- No user-facing message when a stale decision is silently dropped, or a reject is silently skipped by the backend — this is a deliberate scope cut, not an oversight.
- `reject_image()`'s guard change must return `None` (not raise) when the image isn't pending — identical to its existing "image doesn't exist" return, so `IngestionService.resolve()` needs no changes to handle it correctly.

Spec: `docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md`

---

## Task 1: Backend — `reject_image()` requires `status == "pending"`

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py:128-137`
- Test: `tests/integration/test_backend_ingestion_repository.py` (append near the existing `reject_image` tests, currently ending at line 151)
- Create: `Backend/tests/test_ingestion_service.py`

**Interfaces:**
- Consumes: nothing from other tasks in this plan (backend-only, independent of Tasks 2-3).
- Produces: `IngestionRepository.reject_image(image_id) -> Optional[str]` keeps its exact existing signature; only its behavior changes (returns `None` for a non-pending image, in addition to the existing "doesn't exist" case). No caller changes needed — `IngestionService.resolve()` already treats `None` as "skip, nothing to append."

- [ ] **Step 1: Write the failing integration tests**

Add these two tests to `tests/integration/test_backend_ingestion_repository.py`, directly after
`test_reject_image_returns_none_for_unknown_id`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_reject_image_returns_none_for_non_pending_status(db_session):
    batch_id = await _make_run(db_session)
    active_id = await _make_image(db_session, "active", batch_id)

    repo = IngestionRepository(db_session)
    result = await repo.reject_image(active_id)

    assert result is None
    image = await db_session.get(Image, active_id)
    assert image.status == "active"


@pytest.mark.asyncio(loop_scope="session")
async def test_reject_image_returns_none_for_already_rejected_status(db_session):
    batch_id = await _make_run(db_session)
    rejected_id = await _make_image(db_session, "rejected", batch_id)

    repo = IngestionRepository(db_session)
    result = await repo.reject_image(rejected_id)

    assert result is None
    image = await db_session.get(Image, rejected_id)
    assert image.status == "rejected"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_ingestion_repository.py -v -k "test_reject_image_returns_none_for_non_pending_status or test_reject_image_returns_none_for_already_rejected_status"`

Expected: both FAIL — `reject_image()` currently rejects images regardless of current status, so
`result` comes back as the filename (not `None`) and `image.status` ends up `"rejected"` in both
cases.

- [ ] **Step 3: Implement the guard**

In `Backend/app/repositories/ingestion_repository.py`, replace the existing `reject_image` method
(lines 128-137):

```python
    async def reject_image(self, image_id) -> Optional[str]:
        """Flip status to rejected. Returns the filename (for the caller to move the file),
        or None if the image doesn't exist or isn't currently pending (already resolved
        elsewhere -- see
        docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md)."""
        result = await self.session.execute(
            select(Image).where(Image.id == image_id, Image.status == "pending")
        )
        image = result.scalar_one_or_none()
        if image is None:
            return None
        image.status = "rejected"
        await self.session.flush()
        return image.filename
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_ingestion_repository.py -v`

Expected: PASS — run the full file, not just the two new tests. Per this repo's own testing
gotcha, changes to shared repository code should be checked against the full relevant test root;
this file is small enough that running it whole is cheap either way.

- [ ] **Step 5: Write the failing service-level test**

`IngestionService.resolve()` already handles a `None` return from `reject_image()` by skipping
the append to `rejected` (`Backend/app/services/ingestion_service.py:108-112`), but nothing
proves it also skips the file move in that case. Create `Backend/tests/test_ingestion_service.py`:

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


class TestResolveRejectSkipsNonPending:
    async def test_reject_on_non_pending_image_does_not_move_file(self, service, mock_repo):
        image_id = uuid.uuid4()
        mock_repo.reject_image.return_value = None  # not pending, per the repository's own guard

        with patch("Backend.app.services.ingestion_service.image_store") as mock_image_store:
            result = await service.resolve("tier_a", [{"image_id": image_id, "decision": "reject"}])

        mock_image_store.move_to_rejected.assert_not_called()
        assert result["rejected"] == []
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`

Expected: PASS. This test already passes against the current `resolve()` code without any
service-layer change — `resolve()`'s existing `if filename is not None:` check already skips the
move once `reject_image` returns `None`. This step exists purely to add durable regression
coverage for that existing behavior now that Task 1's repository change is what makes `None` a
realistic, common return value (previously it only meant "image doesn't exist," a much rarer
case) — no implementation change is needed for this test to pass.

- [ ] **Step 7: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py tests/integration/test_backend_ingestion_repository.py Backend/tests/test_ingestion_service.py
git commit -m "fix: reject_image() no-ops on non-pending images

Closes the gap where a stale reject decision from an abandoned review
session could resurface and get applied against an image that's no
longer pending -- see
docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md."
```

---

## Task 2: Frontend — clear `decisions` on tier change

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`
- Test: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`

**Interfaces:**
- Consumes: nothing from Task 1. Independent.
- Produces: no new exported interface — internal component behavior only. Task 3 edits the same
  file; do Task 2 first so Task 3's diff applies cleanly on top.

- [ ] **Step 1: Write the failing test**

Add this test inside the existing `describe('IngestionReviewPage', ...)` block in
`Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`, after the
`'submits a reject decision for the pending member and reloads'` test:

```typescript
  it('clears local decisions when the review tier changes', async () => {
    // Regression test: a decision set but never submitted must not survive into a later tier's
    // review, even if the same image_id reappears there in a new cluster -- see
    // docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md.
    const getIngestionRunStatus = vi.fn()
      .mockResolvedValueOnce(mockStatus) // tier_a_review
      .mockResolvedValueOnce({ ...mockStatus, stage: 'tier_b_review' }) // advances after submit's reload
    const tierBClusterReusingSameId: IngestionCluster = {
      members: [
        { image_id: 'pending-2', filename: 'second.jpg', status: 'pending', ocr_text: null },
      ],
      edges: [],
    }
    const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: [] })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce([mockCluster, mockClusterTwo]) // initial tier_a load
      .mockResolvedValueOnce([tierBClusterReusingSameId]) // reload after submit, now tier_b
    const api = makeMockApi({
      getIngestionRunStatus, getIngestionClusters, resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    // Decide pending-1 (mockCluster, will be submitted) and pending-2 (mockClusterTwo, left
    // un-submitted -- this is the stale decision that must not survive the tier flip triggered
    // by pending-1's submit below).
    await user.click(screen.getAllByText('Reject')[0])
    await user.click(screen.getAllByText('Reject')[1])
    await user.click(screen.getAllByText('Submit decisions')[0]) // mockCluster's own submit

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    expect(resolve).toHaveBeenCalledWith('tier_a', [{ image_id: 'pending-1', decision: 'reject' }])

    // Reload landed on tier_b, reusing pending-2's id in a new cluster.
    await waitFor(() => expect(screen.getByText('Ingestion Review — Tier B')).toBeInTheDocument())
    expect(screen.getByText('second.jpg')).toBeInTheDocument()
    // pending-2's stale tier_a Reject must not have survived the tier change.
    expect(screen.getByText('Submit decisions')).toBeDisabled()
    expect(screen.getByText('Reject')).not.toHaveClass('bg-red-600')
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx -t "clears local decisions when the review tier changes"`

Expected: FAIL — `pending-2`'s decision was never submitted (only `mockCluster`'s "Submit
decisions" button was clicked), so it's still present in `decisions` after reload; without the
fix, the reappeared `pending-2` cluster's "Submit decisions" button is enabled and `Reject` shows
the active (`bg-red-600`) class.

- [ ] **Step 3: Implement the fix**

In `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`, add a new `useEffect` directly
after the existing cleanup effect (currently at lines 135-139):

```typescript
  useEffect(() => {
    return () => {
      if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
    }
  }, [])

  useEffect(() => {
    setDecisions({})
  }, [tier])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`

Expected: PASS — full file, since this touches shared page state other tests also exercise.

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "fix: clear ingestion decisions on tier change

A decision set but never submitted during one tier's review must not
survive into a later tier's review, even if the same image_id
reappears there -- see
docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md."
```

---

## Task 3: Frontend — filter decision collection to currently-`pending` members

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`
- Test: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`

**Interfaces:**
- Consumes: the `useEffect` added in Task 2 must already be present (same file); this task's
  diff assumes Task 2 is already applied.
- Produces: no new exported interface — internal component behavior only.

- [ ] **Step 1: Write the failing test**

Add this test after the `'clears local decisions when the review tier changes'` test added in
Task 2:

```typescript
  it('excludes a decided member from submission once its status is no longer pending', async () => {
    // Regression test: a decision set while a member was pending must not be submitted if a
    // reload shows it's since been resolved by a concurrent reviewer in the same tier (no tier
    // change involved -- that case is covered by Task 2's test) -- see
    // docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md.
    const clusterTwoAfterConcurrentResolve: IngestionCluster = {
      members: [
        { image_id: 'pending-2', filename: 'second.jpg', status: 'active', ocr_text: null },
      ],
      edges: [],
    }
    const resolve = vi.fn()
      .mockResolvedValueOnce({ rejected: ['pending-1'], kept: [] }) // pending-1's own submit
      .mockResolvedValueOnce({ rejected: ['pending-3'], kept: [] }) // submit-all afterward
    const getIngestionClusters = vi.fn()
      // Initial load: pending-1, pending-2, pending-3 all pending, each its own cluster.
      .mockResolvedValueOnce([mockCluster, mockClusterTwo, mockClusterThree])
      // Reload after pending-1's submit: pending-2 has since gone "active" (concurrent
      // reviewer); pending-3 is still pending and still undecided in this mock.
      .mockResolvedValueOnce([clusterTwoAfterConcurrentResolve, mockClusterThree])
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus), // tier never changes
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    // Decide all three while all are pending. Cluster/button order: pending-1 (index 0),
    // pending-2 (index 1), pending-3 (index 2).
    await user.click(screen.getAllByText('Reject')[0])
    await user.click(screen.getAllByText('Reject')[1])
    await user.click(screen.getAllByText('Reject')[2])

    // Submit only pending-1's own cluster -- triggers the reload where pending-2 goes active.
    await user.click(screen.getAllByText('Submit decisions')[0])
    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByText('second.jpg')).toBeInTheDocument())

    // pending-2's decision is still in local state (never submitted; no tier change happened,
    // so Task 2's guard doesn't apply here). Submit-all must exclude it now that its status is
    // "active", sending only pending-3.
    await user.click(screen.getByText(/Submit all decisions/))
    await user.click(screen.getByText(/Confirm\?/))

    await waitFor(() => expect(resolve).toHaveBeenCalledTimes(2))
    const [, sentDecisions] = resolve.mock.calls[1]
    expect(sentDecisions).toEqual([{ image_id: 'pending-3', decision: 'reject' }])
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx -t "excludes a decided member from submission once its status is no longer pending"`

Expected: FAIL — today's page-level collection loop
(`IngestionReviewPage.tsx:172-181`) reads every cluster member's `decisions[member.image_id]`
without checking `member.status`, so `sentDecisions` comes back including `pending-2` (now
`active`) alongside `pending-3`, not just `pending-3` alone.

- [ ] **Step 3: Implement the fix**

In `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`, replace `submitCluster`'s
collection loop:

```typescript
  async function submitCluster(clusterIndex: number, cluster: IngestionCluster) {
    const pendingMemberIds = new Set(
      cluster.members.filter((m) => m.status === "pending").map((m) => m.image_id)
    )
    const clusterDecisions: { image_id: string; decision: Decision }[] = []
    for (const [image_id, decision] of Object.entries(decisions)) {
      if (pendingMemberIds.has(image_id) && decision !== undefined) {
        clusterDecisions.push({ image_id, decision })
      }
    }
```

And replace the page-level collection loop:

```typescript
  const allPendingDecisions: { image_id: string; decision: Decision }[] = []
  let clustersWithPendingCount = 0
  for (const cluster of clusters) {
    const before = allPendingDecisions.length
    for (const member of cluster.members) {
      if (member.status !== "pending") continue
      const decision = decisions[member.image_id]
      if (decision !== undefined) allPendingDecisions.push({ image_id: member.image_id, decision })
    }
    if (allPendingDecisions.length > before) clustersWithPendingCount++
  }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`

Expected: PASS — full file.

- [ ] **Step 5: Run the full frontend verification suite**

Run:
```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
vitest run
```

Expected: `tsc -b` clean, `eslint src/` clean (0 warnings), all tests pass.

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "fix: exclude non-pending members from ingestion decision submission

A decision set while a member was pending must not be submitted once
a reload shows it's no longer pending (resolved by a concurrent
reviewer in the same tier) -- see
docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md."
```
