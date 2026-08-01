# Skip Already-Reviewed Duplicate Clusters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `batch/detect_file_duplicates.py` skips flagging anything in a duplicate cluster if any
member of that cluster is already flagged, treating that as a signal a human already reviewed it.
Requires a new `ImageExtrasRepository.get_flags_bulk()` — the repository is currently write-only.

**Architecture:** `get_flags_bulk(image_ids) -> dict` does one `SELECT ... WHERE image_id IN (...)`
query, returning every requested id as a key (missing rows default to `False`). A new pure
function, `cluster_already_handled(cluster, flags) -> bool`, holds the decision logic and is unit
tested without a DB. `main()`'s Phase 4 gathers every id across all 2+-member clusters, bulk-fetches
their flags once, and skips a cluster (before the more expensive `files_are_identical` check) when
the helper says so.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-08-01-duplicate-cluster-skip-if-flagged-design.md`

## Global Constraints

- No CLI flag/opt-out — this is the new unconditional default behavior.
- The check considers the **whole cluster**, including the would-be "keeper", not just the
  would-be-flagged duplicates.
- The skip-check happens **before** `files_are_identical` in the per-cluster loop, so a skipped
  cluster doesn't pay that cost either.
- `dedupe_in_batch`/`dedupe_cross_corpus`-style hashing/clustering logic elsewhere in this file is
  unchanged — only Phase 4 (verify content and flag duplicates) is touched.
- No broader test coverage added for parts of `detect_file_duplicates.py` this plan doesn't touch
  — the script currently has zero test coverage of any kind; this plan adds targeted coverage for
  the new logic only.

---

### Task 1: `ImageExtrasRepository.get_flags_bulk()`

**Files:**
- Modify: `repository/image_extras.py`
- Test: `tests/integration/test_image_extras_repository.py` (new)

**Interfaces:**
- Produces: `ImageExtrasRepository.get_flags_bulk(image_ids: list) -> dict`. Task 2 depends on this
  exact method name and its "every requested id gets a key, defaulting to `False`" guarantee.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_image_extras_repository.py`:

```python
"""
Integration tests for repository/image_extras.py's get_flags_bulk().

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid

import pytest

from repository.image_extras import ImageExtrasRepository
from Storage.models import Image


@pytest.mark.asyncio(loop_scope="session")
async def test_get_flags_bulk_returns_correct_status_for_each_id(db_session):
    flagged_image = Image(filename=f"flagged-{uuid.uuid4()}.jpg")
    unflagged_image = Image(filename=f"unflagged-{uuid.uuid4()}.jpg")
    untouched_image = Image(filename=f"untouched-{uuid.uuid4()}.jpg")
    db_session.add_all([flagged_image, unflagged_image, untouched_image])
    await db_session.flush()

    repo = ImageExtrasRepository(db_session)
    await repo.set_flagged(flagged_image.id, True)
    await repo.set_flagged(unflagged_image.id, False)
    # untouched_image gets no image_extras row at all

    flags = await repo.get_flags_bulk([flagged_image.id, unflagged_image.id, untouched_image.id])

    assert flags == {
        flagged_image.id: True,
        unflagged_image.id: False,
        untouched_image.id: False,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_get_flags_bulk_empty_list_returns_empty_dict(db_session):
    repo = ImageExtrasRepository(db_session)

    flags = await repo.get_flags_bulk([])

    assert flags == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_image_extras_repository.py -v`
Expected: FAIL — `AttributeError: 'ImageExtrasRepository' object has no attribute 'get_flags_bulk'`.

- [ ] **Step 3: Implement `get_flags_bulk`**

Replace `repository/image_extras.py`'s full content:

```python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from Storage.models import ImageExtras


class ImageExtrasRepository:

    def __init__(self, session):
        self.session = session

    async def set_flagged(self, image_id, flagged: bool) -> None:
        stmt = (
            insert(ImageExtras)
            .values(image_id=image_id, flagged=flagged)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"flagged": flagged},
            )
        )
        await self.session.execute(stmt)

    async def get_flags_bulk(self, image_ids: list) -> dict:
        """Bulk-fetch flagged status for a set of image_ids in one query. Every id in
        image_ids is guaranteed a key in the result -- an id with no image_extras row
        at all (never flagged/unflagged) maps to False."""
        result = await self.session.execute(
            select(ImageExtras.image_id, ImageExtras.flagged)
            .where(ImageExtras.image_id.in_(image_ids))
        )
        flags = {image_id: bool(flagged) for image_id, flagged in result.all()}
        return {image_id: flags.get(image_id, False) for image_id in image_ids}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_image_extras_repository.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add repository/image_extras.py tests/integration/test_image_extras_repository.py
git commit -m "feat: add ImageExtrasRepository.get_flags_bulk()"
```

---

### Task 2: Skip already-flagged clusters in `detect_file_duplicates.py`

**Files:**
- Modify: `batch/detect_file_duplicates.py`
- Test: `batch/tests/test_detect_file_duplicates.py` (new)

**Interfaces:**
- Consumes: `ImageExtrasRepository.get_flags_bulk(image_ids) -> dict` (Task 1).
- Produces: `cluster_already_handled(cluster: list, flags: dict) -> bool` (new pure function, no
  DB/filesystem access).

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_detect_file_duplicates.py`:

```python
"""
Unit tests for batch/detect_file_duplicates.py's cluster_already_handled() -- pure
decision logic extracted for testability, no DB or filesystem involved.
"""
from batch.detect_file_duplicates import cluster_already_handled


def test_returns_false_when_no_member_is_flagged():
    cluster = ["a", "b", "c"]
    flags = {"a": False, "b": False, "c": False}

    assert cluster_already_handled(cluster, flags) is False


def test_returns_true_when_a_duplicate_is_flagged():
    cluster = ["keeper", "dup1", "dup2"]
    flags = {"keeper": False, "dup1": True, "dup2": False}

    assert cluster_already_handled(cluster, flags) is True


def test_returns_true_when_the_keeper_itself_is_flagged():
    cluster = ["keeper", "dup1"]
    flags = {"keeper": True, "dup1": False}

    assert cluster_already_handled(cluster, flags) is True


def test_missing_id_in_flags_defaults_to_not_flagged():
    cluster = ["a", "b"]
    flags = {"a": False}  # "b" absent

    assert cluster_already_handled(cluster, flags) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_detect_file_duplicates.py -v`
Expected: FAIL — `ImportError: cannot import name 'cluster_already_handled' from 'batch.detect_file_duplicates'`.

- [ ] **Step 3: Add `cluster_already_handled` and wire it into `main()`**

In `batch/detect_file_duplicates.py`, add this function before `main()` (after the imports):

```python
def cluster_already_handled(cluster: list, flags: dict) -> bool:
    """True if any member of this cluster is already flagged -- treat the whole
    cluster as already reviewed by a human, and skip flagging anything else in it."""
    return any(flags.get(mid, False) for mid in cluster)
```

Replace the whole "Phase 4" block inside `main()`:

```python
    # ── Phase 4: verify content and flag duplicates ──────────────────────────
    async with AsyncSessionLocal() as session:
        extras_repo = ImageExtrasRepository(session)

        clusters = [uf.get_cluster(root) for root in uf.list_clusters()]
        clusters = [c for c in clusters if len(c) >= 2]

        all_member_ids = [mid for cluster in clusters for mid in cluster]
        flags = await extras_repo.get_flags_bulk(all_member_ids)

        for cluster in clusters:
            if cluster_already_handled(cluster, flags):
                names = [hashed[mid][0] for mid in cluster]
                print(f"  cluster {len(cluster)}: already has a flagged member, skipping ({names})")
                metrics.increment("clusters.skipped_already_flagged")
                continue

            paths = [os.path.join(base_path, hashed[mid][0]) for mid in cluster]

            if not files_are_identical(paths):
                # SHA-256 collision is astronomically unlikely; more likely a read error
                names = [hashed[mid][0] for mid in cluster]
                print(f"  WARNING: content mismatch despite identical hash in cluster {names} — skipping")
                metrics.increment("warning.content_mismatch")
                continue

            # Keep the oldest (earliest created_at); flag the rest
            by_age = sorted(cluster, key=lambda mid: hashed[mid][2])
            keeper, *duplicates = by_age

            keeper_name = hashed[keeper][0]
            dup_names = [hashed[d][0] for d in duplicates]
            print(f"  cluster {len(cluster)}: keep={keeper_name}  flag={dup_names}")

            for dup_id in duplicates:
                await extras_repo.set_flagged(dup_id, True)
                metrics.increment("flagged")

            metrics.increment("clusters.found")

        await session.commit()
```

(This is the same logic as before, restructured so `clusters` is computed once upfront — enabling
the single bulk flag fetch across every cluster member before the loop — with the new skip-check
as the loop's first branch. `if len(cluster) < 2: continue` from the original inline loop is now
the upfront list-comprehension filter `[c for c in clusters if len(c) >= 2]` instead — same effect,
computed once.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_detect_file_duplicates.py -v`
Expected: all PASS.

- [ ] **Step 5: Run the full `batch/tests/` root**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/ -v`
Expected: all PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add batch/detect_file_duplicates.py batch/tests/test_detect_file_duplicates.py
git commit -m "feat: detect_file_duplicates skips clusters with an already-flagged member"
```

## Self-Review Notes

- **Spec coverage:** `get_flags_bulk`'s exact contract (one query, every id gets a key, missing
  rows default `False`) — Task 1; `cluster_already_handled`'s whole-cluster check (including the
  keeper) and its wiring before `files_are_identical` — Task 2. Every part of the spec has a
  corresponding task.
- **Type consistency:** `get_flags_bulk`'s return shape (`dict[image_id, bool]`) matches exactly
  what `cluster_already_handled` consumes (`flags.get(mid, False)`) and what both test files assert
  against.
- **Existing-code audit:** confirmed neither `repository/image_extras.py` nor
  `batch/detect_file_duplicates.py` has any existing test file — both Task 1 and Task 2's test
  files are genuinely new, not modifications of something pre-existing that needed auditing for
  breakage (unlike the last two plans in this series, which each had to fix an existing test file).
