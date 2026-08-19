# Persisted Not-Duplicate Decisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a reviewer mark a duplicate cluster as "not duplicates" so `clusterize.py` never re-clusters those images again, without triggering a rebuild or filtering the review query.

**Architecture:** A new durable `duplicate_decisions` table stores confirmed-not-duplicate pairs (always `LEAST`/`GREATEST`-normalized). `clusterize.py`'s existing candidate-pair query excludes any pair present in that table. The review API exposes a single mutation — "dismiss this cluster" — which looks up the cluster's current members server-side and records every `C(N, 2)` pair; a companion "undo" endpoint deletes specific pairs. The frontend removes the dismissed row's content optimistically (session-local only, no backend read-time filtering) and offers an inline undo via a toast, plus a minimal admin-page listing for undoing decisions from past sessions.

**Tech Stack:** FastAPI + SQLAlchemy async (Backend), Alembic (migrations), React + TypeScript + Vitest (Frontend), pytest (backend unit + integration tests), JSON Schema → TypeScript/Kotlin codegen (shared/schemas/).

**Spec:** `docs/superpowers/specs/2026-08-19-duplicate-dismissal-decisions-design.md`

## Global Constraints

- `duplicate_decisions` is **not** `tmp_`-prefixed and must survive `rebuild_duplicates --full`'s wipe of `tmp_duplicates` — it is durable source data, never touched by any `tmp_*` cleanup.
- `image_id1`/`image_id2` are always stored `LEAST`/`GREATEST`-normalized (the smaller UUID first) in every table/request/response that carries a pair — never store or compare both orderings.
- **No read-time filtering** of dismissed clusters in `GET /api/images/duplicates` — out of scope per spec.
- **No `clusterize` rerun** triggered by a dismiss/undo action — effect is visible only on the next manually-triggered `clusterize` run.
- The UI exposes **only whole-cluster dismissal**; the durable primitive is always the pair. A dismiss action always re-reads the cluster's current members server-side — never trust a client-supplied member list.
- Frontend optimistic UI state is session-local only — no `localStorage`. A page refresh must reflect real server state.
- Repositories never call `session.commit()` — `get_async_db` (backend) / the caller (batch) owns commit timing, per this repo's layering convention.

---

### Task 1: `duplicate_decisions` table — ORM model and migration

**Files:**
- Modify: `Storage/models.py` (add `DuplicateDecision` class, near `TmpDuplicates`/`TmpImageClusters` around line 218)
- Create: `Storage/alembic/versions/<generated>_duplicate_decisions_table.py`

**Interfaces:**
- Produces: `Storage.models.DuplicateDecision` — columns `id` (UUID pk), `image_id1` (UUID, FK `images.id`, `ondelete="CASCADE"`), `image_id2` (UUID, FK `images.id`, `ondelete="CASCADE"`), `decided_at` (DateTime, `server_default=func.now()`). Table name `duplicate_decisions`. Unique constraint on `(image_id1, image_id2)`.

- [ ] **Step 1: Add the ORM model**

In `Storage/models.py`, immediately after the `TmpImageClusters` class (ends around line 224), add:

```python
class DuplicateDecision(Base):
    """A human-confirmed "these two images are not duplicates" decision. Durable source
    data -- unlike tmp_duplicates/tmp_clusters, this table is never dropped or wiped by
    any batch script, including rebuild_duplicates.py's --full mode. clusterize.py
    excludes any pair present here from its union-find. See
    docs/superpowers/specs/2026-08-19-duplicate-dismissal-decisions-design.md.

    image_id1/image_id2 are always stored as (LEAST(a, b), GREATEST(a, b)), mirroring
    TmpDuplicates' own convention, so a pair is only ever represented once."""

    __tablename__ = "duplicate_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"), index=True)
    image_id1 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)
    image_id2 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)

    decided_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("image_id1", "image_id2", name="uq_duplicate_decisions_pair"),
    )
```

This matches `TmpDuplicates`' exact column style (same file, ~line 176-215) — `uuid`, `text`, `func`, `Column`, `UUID`, `ForeignKey`, `UniqueConstraint` are all already imported at the top of `Storage/models.py` (confirm by checking the existing `TmpDuplicates` class uses the same names unqualified).

- [ ] **Step 2: Generate the migration scaffold**

From `Storage/`, with `DATABASE_URL` set for any environment (the migration itself is schema-only, doesn't touch data):

```powershell
Get-Content ..\environments\.env.metal | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic revision -m "add duplicate_decisions table"
```

This creates a new file in `Storage/alembic/versions/` with an auto-generated revision id and `down_revision` already set to the current head — do not hand-write these ids.

- [ ] **Step 3: Fill in the migration**

Open the generated file and replace its `upgrade()`/`downgrade()` bodies (keep the auto-generated header/revision variables as-is):

```python
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


def upgrade() -> None:
    op.create_table(
        'duplicate_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text('gen_random_uuid()')),
        sa.Column('image_id1', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('image_id2', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_foreign_key(
        'duplicate_decisions_image_id1_fkey', 'duplicate_decisions', 'images',
        ['image_id1'], ['id'], ondelete='CASCADE',
    )
    op.create_foreign_key(
        'duplicate_decisions_image_id2_fkey', 'duplicate_decisions', 'images',
        ['image_id2'], ['id'], ondelete='CASCADE',
    )
    op.create_unique_constraint(
        'uq_duplicate_decisions_pair', 'duplicate_decisions', ['image_id1', 'image_id2'],
    )
    op.create_index('ix_duplicate_decisions_image_id1', 'duplicate_decisions', ['image_id1'])
    op.create_index('ix_duplicate_decisions_image_id2', 'duplicate_decisions', ['image_id2'])


def downgrade() -> None:
    op.drop_table('duplicate_decisions')
```

(Mirrors `Storage/alembic/versions/fa057003a158_persistent_tmp_duplicates_table.py`'s exact structure for `tmp_duplicates`, minus the columns this table doesn't need.)

- [ ] **Step 4: Apply and verify the migration**

From `Storage/` (env vars still set from Step 2):

```powershell
alembic upgrade head
```

Expected: no errors, migration applies. Then verify downgrade/re-upgrade both work cleanly:

```powershell
alembic downgrade -1
alembic upgrade head
```

Expected: both succeed with no errors, leaving the schema at head.

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/
git commit -m "feat: add duplicate_decisions table for persisted not-duplicate decisions"
```

---

### Task 2: `clusterize.py` excludes decided pairs

**Files:**
- Modify: `batch/clusterize.py`
- Create: `tests/integration/test_clusterize.py`

**Interfaces:**
- Consumes: `Storage.models.DuplicateDecision` (Task 1).
- Produces: `batch.clusterize.cluster_active_library(session) -> None` — the union-find/write logic extracted out of `_process()` so integration tests can call it with an injected session, exactly mirroring `batch/rebuild_duplicates.py`'s `_process()`/`rebuild_active_library()` split. `_process()` keeps its existing signature and behavior (still called the same way by `main()`); `get_duplicate_pairs`'s signature is unchanged.

- [ ] **Step 1: Write the failing integration tests**

Create `tests/integration/test_clusterize.py`:

```python
"""
Integration tests for batch/clusterize.py -- requires a live PostgreSQL instance.
Same DB-fixture pattern as tests/integration/test_rebuild_duplicates.py.
"""
import uuid

import pytest
from sqlalchemy import select

from batch.clusterize import cluster_active_library
from Storage.models import DuplicateDecision, Embedding, Image, TmpDuplicates, TmpImageClusters


async def _insert_image(session, status: str = "active") -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    return image.id


def _normalize(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if a < b else (b, a)


async def _insert_pair(session, a: uuid.UUID, b: uuid.UUID, distance: float) -> None:
    id1, id2 = _normalize(a, b)
    session.add(TmpDuplicates(image_id1=id1, image_id2=id2, distance=distance))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_decided_pair_is_excluded_from_clustering(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)
    id1, id2 = _normalize(a, b)
    db_session.add(DuplicateDecision(image_id1=id1, image_id2=id2))
    await db_session.flush()

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters))).scalars().all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_undecided_pair_still_clusters(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b}


@pytest.mark.asyncio(loop_scope="session")
async def test_decision_only_excludes_the_decided_pair_not_the_whole_cluster(db_session):
    # Chain a-b-c: a-b decided not-duplicate, b-c still undecided. b-c should still cluster;
    # a should end up alone (dropped -- no surviving edge at all involves a).
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.02)
    await _insert_pair(db_session, b, c, 0.02)
    id1, id2 = _normalize(a, b)
    db_session.add(DuplicateDecision(image_id1=id1, image_id2=id2))
    await db_session.flush()

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {b, c}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_clusterize.py -v
```

Expected: `ImportError`/`AttributeError` — `cluster_active_library` doesn't exist yet (only `_process` does), and `DuplicateDecision` filtering isn't implemented.

- [ ] **Step 3: Refactor `_process` and add the decision filter**

In `batch/clusterize.py`:

1. Add `DuplicateDecision` to the existing import: change
   `from Storage.models import Image, TmpDuplicates, TmpImageClusters`
   to
   `from Storage.models import DuplicateDecision, Image, TmpDuplicates, TmpImageClusters`

2. Replace the current `_process()` function body with a new `cluster_active_library(session)` function containing everything the old `_process()` did except opening the session and committing, and make `_process()` a thin wrapper — mirroring `batch/rebuild_duplicates.py`'s `_process()`/`rebuild_active_library()` split exactly:

```python
async def cluster_active_library(session) -> None:

    print("Cleaning up clusters...")
    query = (
        delete(TmpImageClusters)
    )
    await session.execute(query)

    print("Reading images...")
    # Select all images and build image_id -> int id dictionary (and reverse)
    img_id_to_int_id, mapping_reverse = await get_images_ids(session)
    print(f"Total images: {len(img_id_to_int_id)}")

    print("Reading duplicates...")
    # Select all duplicate pairs with distance < PROXIMITY_THRESHOLD, int-id mapped
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD)
    print(f"Total connections: {len(pairs)}")

    uf = UnionFind()
    pairs_by_member: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for id1, id2, distance in pairs:
        uf.connect(id1, id2)
        pairs_by_member[id1].append((id2, distance))
        pairs_by_member[id2].append((id1, distance))

    splitting = settings.CLUSTERING.SPLITTING

    print("Building graph...")
    # Traverse UnionFind, splitting oversized clusters and dropping singletons, and
    # mark the resulting clusters
    for root in uf.list_clusters():
        members = uf.get_cluster(root)
        if splitting.ENABLED:
            groups = resolve_cluster(
                members,
                pairs_by_member,
                PROXIMITY_THRESHOLD,
                splitting.DECREMENT,
                splitting.FLOOR,
                splitting.MAX_CLUSTER_SIZE,
            )
        else:
            groups = [members]

        for group in groups:
            # min() is unique across the whole run -- finalized groups always
            # partition disjoint member sets, so no two groups can share it.
            cluster_id = min(group)
            for member in group:
                img_id = mapping_reverse[member]
                session.add(TmpImageClusters(cluster_id=cluster_id, image_id=img_id))

    print("Saving results...")


async def _process() -> None:
    async with AsyncSessionLocal() as session:
        await cluster_active_library(session)
        await session.commit()
```

3. Add the decision filter to `get_duplicate_pairs`:

```python
async def get_duplicate_pairs(session, mapping, threshold) -> list[tuple[int, int, float]]:
    decided_pair_exists = (
        select(DuplicateDecision.id)
        .where(
            DuplicateDecision.image_id1 == TmpDuplicates.image_id1,
            DuplicateDecision.image_id2 == TmpDuplicates.image_id2,
        )
        .exists()
    )
    query = (
        select(
            TmpDuplicates.image_id1,
            TmpDuplicates.image_id2,
            TmpDuplicates.distance,
        ).where(
            TmpDuplicates.distance < threshold,
            TmpDuplicates.image_id1 != TmpDuplicates.image_id2,
            ~decided_pair_exists,
        )
    )
    duplicates = await session.execute(query)
    # mapping is active-only (see get_images_ids) -- drop any pair touching a
    # pending/rejected image rather than KeyError on it.
    return [
        (mapping[id1], mapping[id2], distance)
        for id1, id2, distance in duplicates
        if id1 in mapping and id2 in mapping
    ]
```

Both `tmp_duplicates` and `duplicate_decisions` store pairs `LEAST`/`GREATEST`-normalized already, so this is a direct equality correlation — no re-normalization needed at query time.

- [ ] **Step 4: Run the integration tests to verify they pass**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_clusterize.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Verify the existing unit tests still pass unchanged**

```bash
cd Backend && cd .. && pytest batch/tests/test_clusterize.py -v
```

Expected: all pass — `_process` still exists with the same external behavior, so `TestMain`'s `patch.object(module, "_process", ...)` tests are unaffected; `TestResolveCluster` is untouched by this task.

- [ ] **Step 6: Commit**

```bash
git add batch/clusterize.py tests/integration/test_clusterize.py
git commit -m "feat: clusterize.py excludes pairs recorded in duplicate_decisions"
```

---

### Task 3: Shared schemas and generated types

**Files:**
- Create: `shared/schemas/duplicatepair.schema.json`
- Create: `shared/schemas/duplicatedismissresponse.schema.json`
- Create: `shared/schemas/duplicateundodismissrequest.schema.json`
- Create: `shared/schemas/duplicatedecisionitem.schema.json`
- Create: `shared/schemas/duplicatedecisionlistresponse.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Modify (generated, do not hand-edit content): `Frontend/memes-frontend/src/types/generated/all.d.ts`
- Modify (generated, do not hand-edit content): `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`

**Interfaces:**
- Produces (TypeScript, for Task 6/7/8 to import from `../types/generated/all`): `DuplicatePair { image_id1: string; image_id2: string }`, `DuplicateDismissResponse { pairs: DuplicatePair[] }`, `DuplicateUndoDismissRequest { pairs: DuplicatePair[] }`, `DuplicateDecisionItem { image_id1: string; filename1: string; image_id2: string; filename2: string; decided_at: string }`, `DuplicateDecisionListResponse { items: DuplicateDecisionItem[]; total: number }`.

- [ ] **Step 1: Add the schema files**

`shared/schemas/duplicatepair.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "duplicatepair.schema.json",
  "title": "DuplicatePair",
  "type": "object",
  "properties": {
    "image_id1": { "type": "string" },
    "image_id2": { "type": "string" }
  },
  "required": ["image_id1", "image_id2"]
}
```

`shared/schemas/duplicatedismissresponse.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "duplicatedismissresponse.schema.json",
  "title": "DuplicateDismissResponse",
  "type": "object",
  "properties": {
    "pairs": { "type": "array", "items": { "$ref": "./duplicatepair.schema.json" } }
  },
  "required": ["pairs"]
}
```

`shared/schemas/duplicateundodismissrequest.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "duplicateundodismissrequest.schema.json",
  "title": "DuplicateUndoDismissRequest",
  "type": "object",
  "properties": {
    "pairs": { "type": "array", "items": { "$ref": "./duplicatepair.schema.json" } }
  },
  "required": ["pairs"]
}
```

`shared/schemas/duplicatedecisionitem.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "duplicatedecisionitem.schema.json",
  "title": "DuplicateDecisionItem",
  "type": "object",
  "properties": {
    "image_id1": { "type": "string" },
    "filename1": { "type": "string" },
    "image_id2": { "type": "string" },
    "filename2": { "type": "string" },
    "decided_at": { "type": "string" }
  },
  "required": ["image_id1", "filename1", "image_id2", "filename2", "decided_at"]
}
```

`shared/schemas/duplicatedecisionlistresponse.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "duplicatedecisionlistresponse.schema.json",
  "title": "DuplicateDecisionListResponse",
  "type": "object",
  "properties": {
    "items": { "type": "array", "items": { "$ref": "./duplicatedecisionitem.schema.json" } },
    "total": { "type": "integer" }
  },
  "required": ["items", "total"]
}
```

- [ ] **Step 2: Register the new schemas in `all.schema.json`**

In `shared/schemas/all.schema.json`, change the last two lines (currently):
```json
    "BatchNamesResponse":      { "$ref": "batchnamesresponse.schema.json" }
  }
}
```
to:
```json
    "BatchNamesResponse":      { "$ref": "batchnamesresponse.schema.json" },
    "DuplicatePair":                 { "$ref": "duplicatepair.schema.json" },
    "DuplicateDismissResponse":       { "$ref": "duplicatedismissresponse.schema.json" },
    "DuplicateUndoDismissRequest":    { "$ref": "duplicateundodismissrequest.schema.json" },
    "DuplicateDecisionItem":          { "$ref": "duplicatedecisionitem.schema.json" },
    "DuplicateDecisionListResponse":  { "$ref": "duplicatedecisionlistresponse.schema.json" }
  }
}
```

- [ ] **Step 3: Regenerate TypeScript types**

```bash
cd Frontend
bash generate-types.sh
```

Expected: `memes-frontend/src/types/generated/all.d.ts` changes to include the 5 new interfaces.

- [ ] **Step 4: Regenerate Android DTOs**

From repo root:
```bash
python AndroidClient/scripts/generate_dtos.py
```

Expected: `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` changes to include 5 new `data class`es (`DuplicatePair`, `DuplicateDismissResponse`, `DuplicateUndoDismissRequest`, `DuplicateDecisionItem`, `DuplicateDecisionListResponse`).

- [ ] **Step 5: Verify both generators are idempotent (matches CI's drift check)**

```bash
cd Frontend
bash generate-types.sh
git diff --exit-code memes-frontend/src/types/generated/
cd ..
python AndroidClient/scripts/generate_dtos.py
git diff --exit-code AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
```

Expected: both `git diff --exit-code` calls succeed (no output, exit 0) — running the generators again produces byte-identical output, confirming what gets committed now is exactly what CI would also produce.

- [ ] **Step 6: Commit**

```bash
git add shared/schemas/ Frontend/memes-frontend/src/types/generated/ AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
git commit -m "feat: add shared schemas for duplicate-decision types"
```

---

### Task 4: Backend repository and service methods

**Files:**
- Create: `Backend/app/repositories/duplicate_decisions_repository.py`
- Modify: `Backend/app/repositories/image_repository.py` (add `get_cluster_member_ids`)
- Modify: `Backend/app/services/image_service.py` (add `dismiss_cluster`, `undo_dismiss`, `list_duplicate_decisions`; constructor now takes a second required arg)
- Modify: `Backend/tests/test_image_service.py` (update the `service` fixture for the new constructor arg)
- Test: `Backend/tests/test_image_service.py` (new test classes for the three new methods)

**Interfaces:**
- Consumes: `Storage.models.DuplicateDecision`, `Storage.models.TmpImageClusters`, `Storage.models.Image` (Task 1, pre-existing).
- Produces:
  - `DuplicateDecisionsRepository(session)` with `async record_decisions_bulk(pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None`, `async delete_decisions(pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None`, `async list_recent(limit: int, offset: int) -> tuple[list[tuple[uuid.UUID, str, uuid.UUID, str, datetime]], int]`.
  - `ImageRepository.get_cluster_member_ids(self, cluster_id: int) -> list[uuid.UUID]`.
  - `ImageService.__init__(self, repo: ImageRepository, decision_repo: DuplicateDecisionsRepository)` — **breaking change to the constructor signature**, both call sites (`Backend/app/api/images.py`'s `get_image_service`, `Backend/tests/test_image_service.py`'s `service` fixture) must be updated in this same task.
  - `ImageService.dismiss_cluster(self, cluster_id: int) -> list[tuple[uuid.UUID, uuid.UUID]]` (raises `fastapi.HTTPException(404, ...)` if the cluster has no current active members).
  - `ImageService.undo_dismiss(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None`.
  - `ImageService.list_duplicate_decisions(self, limit: int, offset: int) -> tuple[list[tuple], int]` (passthrough to the repository).

- [ ] **Step 1: Write the failing service tests**

Add to `Backend/tests/test_image_service.py`. These test classes reference a `mock_decision_repo`
fixture — defined once, in Step 2 below, alongside the other fixture updates; add these classes
first, they just won't pass collection/run cleanly until Step 2 lands too (that's expected, this
is still the "write the failing test" step):

```python
class TestDismissCluster:
    async def test_dismiss_generates_all_pairs_for_current_members(self, service, mock_repo, mock_decision_repo):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.get_cluster_member_ids.return_value = [a, b, c]

        pairs = await service.dismiss_cluster(141)

        mock_repo.get_cluster_member_ids.assert_awaited_once_with(141)
        assert sorted(pairs) == sorted([(a, b), (a, c), (b, c)])
        mock_decision_repo.record_decisions_bulk.assert_awaited_once()
        recorded = mock_decision_repo.record_decisions_bulk.call_args.args[0]
        assert sorted(recorded) == sorted(pairs)

    async def test_dismiss_raises_404_for_unknown_cluster(self, service, mock_repo):
        mock_repo.get_cluster_member_ids.return_value = []

        with pytest.raises(HTTPException) as exc_info:
            await service.dismiss_cluster(999)

        assert exc_info.value.status_code == 404


class TestUndoDismiss:
    async def test_undo_delegates_to_repository(self, service, mock_decision_repo):
        pairs = [(uuid.uuid4(), uuid.uuid4())]

        await service.undo_dismiss(pairs)

        mock_decision_repo.delete_decisions.assert_awaited_once_with(pairs)


class TestListDuplicateDecisions:
    async def test_list_delegates_to_repository(self, service, mock_decision_repo):
        mock_decision_repo.list_recent.return_value = ([], 0)

        result = await service.list_duplicate_decisions(limit=10, offset=5)

        mock_decision_repo.list_recent.assert_awaited_once_with(10, 5)
        assert result == ([], 0)
```

At the top of the file, add: `from fastapi import HTTPException` (if not already imported — check first; the file already imports `HTTPException` per its `TestGetSimilarImageMode` class, so this may be a no-op).

- [ ] **Step 2: Update the `service` fixture for the new constructor signature**

In `Backend/tests/test_image_service.py`, change:
```python
@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo):
    return ImageService(mock_repo)
```
to:
```python
@pytest.fixture
def mock_repo():
    return AsyncMock()


@pytest.fixture
def mock_decision_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo, mock_decision_repo):
    return ImageService(mock_repo, mock_decision_repo)
```

(This is where `mock_decision_repo`, referenced by Step 1's test classes, is actually defined.)

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd Backend && pytest tests/test_image_service.py -v
```

Expected: failures — `ImageService.__init__` doesn't accept a second argument yet, and `dismiss_cluster`/`undo_dismiss`/`list_duplicate_decisions` don't exist.

- [ ] **Step 4: Create the repository**

Create `Backend/app/repositories/duplicate_decisions_repository.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from Storage.models import DuplicateDecision, Image


class DuplicateDecisionsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _normalize(image_id1: uuid.UUID, image_id2: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
        return (image_id1, image_id2) if image_id1 < image_id2 else (image_id2, image_id1)

    async def record_decisions_bulk(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        if not pairs:
            return
        normalized = [self._normalize(a, b) for a, b in pairs]
        stmt = pg_insert(DuplicateDecision).values([
            {"image_id1": a, "image_id2": b} for a, b in normalized
        ]).on_conflict_do_nothing(index_elements=["image_id1", "image_id2"])
        await self.session.execute(stmt)

    async def delete_decisions(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        for a, b in pairs:
            id1, id2 = self._normalize(a, b)
            await self.session.execute(
                delete(DuplicateDecision).where(
                    DuplicateDecision.image_id1 == id1,
                    DuplicateDecision.image_id2 == id2,
                )
            )

    async def list_recent(
        self, limit: int, offset: int
    ) -> tuple[list[tuple[uuid.UUID, str, uuid.UUID, str, datetime]], int]:
        img1 = aliased(Image)
        img2 = aliased(Image)
        query = (
            select(
                DuplicateDecision.image_id1,
                img1.filename,
                DuplicateDecision.image_id2,
                img2.filename,
                DuplicateDecision.decided_at,
            )
            .join(img1, img1.id == DuplicateDecision.image_id1)
            .join(img2, img2.id == DuplicateDecision.image_id2)
            .order_by(DuplicateDecision.decided_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(query)).all()
        total = (await self.session.execute(select(func.count()).select_from(DuplicateDecision))).scalar_one()
        return [tuple(row) for row in rows], total
```

Note: this class has no dedicated unit tests of its own — matches this repo's existing convention (no `Backend/tests/test_*_repository.py` files exist; repositories are exercised indirectly via service-level tests that mock them, per `test_ingestion_service.py`'s docstring, and via integration tests where warranted). Its DB-touching correctness (the `ON CONFLICT DO NOTHING` idempotency, the cascade-delete FK behavior) is exercised end-to-end by Task 2's `tests/integration/test_clusterize.py`, which inserts `DuplicateDecision` rows directly and confirms `clusterize.py` respects them.

- [ ] **Step 5: Add `get_cluster_member_ids` to `ImageRepository`**

In `Backend/app/repositories/image_repository.py`, add this method (place it near `get_duplicates_clustered`, e.g. directly after `get_duplicates_clustered_before`):

```python
    async def get_cluster_member_ids(self, cluster_id: int) -> list[uuid.UUID]:
        img = aliased(Image)
        cluster = aliased(TmpImageClusters)
        query = (
            select(img.id)
            .select_from(cluster)
            .join(img, img.id == cluster.image_id)
            .where(cluster.cluster_id == cluster_id, img.status == "active")
        )
        rows = await self.session.execute(query)
        return [row[0] for row in rows]
```

(`aliased`, `select`, `Image`, `TmpImageClusters` are all already imported at the top of this file.)

- [ ] **Step 6: Add the three methods to `ImageService`, update its constructor**

In `Backend/app/services/image_service.py`:

1. Add the import: `from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository`
2. Add `import uuid` if not already present (check — the file's current top-of-file imports don't include it).
3. Change the constructor:
```python
class ImageService:
    def __init__(self, repo: ImageRepository, decision_repo: DuplicateDecisionsRepository):
        self.repo = repo
        self.decision_repo = decision_repo
```
4. Add the three methods (place them near `get_duplicates_clustered`):
```python
    async def dismiss_cluster(self, cluster_id: int) -> list[tuple[uuid.UUID, uuid.UUID]]:
        member_ids = await self.repo.get_cluster_member_ids(cluster_id)
        if not member_ids:
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found")
        pairs = [
            (member_ids[i], member_ids[j])
            for i in range(len(member_ids))
            for j in range(i + 1, len(member_ids))
        ]
        await self.decision_repo.record_decisions_bulk(pairs)
        return pairs

    async def undo_dismiss(self, pairs: list[tuple[uuid.UUID, uuid.UUID]]) -> None:
        await self.decision_repo.delete_decisions(pairs)

    async def list_duplicate_decisions(self, limit: int, offset: int) -> tuple[list[tuple], int]:
        return await self.decision_repo.list_recent(limit, offset)
```

(`HTTPException` is already imported at the top of this file, per its existing `get_similar` 404 usage.)

- [ ] **Step 7: Update the one other constructor call site**

In `Backend/app/api/images.py`'s `get_image_service` (Task 5 will add the router endpoints, but this dependency function must be updated now or `ImageService(repository)` will fail with a missing-argument error the moment anything imports this module):

```python
from Backend.app.repositories.duplicate_decisions_repository import DuplicateDecisionsRepository

async def get_image_service(
    db: AsyncSessionLocal = Depends(get_async_db)
) -> AsyncGenerator[ImageService, None]:
    repository = ImageRepository(db)
    decision_repository = DuplicateDecisionsRepository(db)
    service = ImageService(repository, decision_repository)
    try:
        yield service
    finally:
        pass
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd Backend && pytest tests/test_image_service.py -v
```

Expected: all tests PASS, including the pre-existing `TestGetSimilarImageMode` class (confirming the constructor change didn't break it).

- [ ] **Step 9: Confirm the server still starts without import errors**

```bash
set WATCHFILES_FORCE_POLLING=1
uvicorn Backend.app.main:app --reload-dir Backend/app --env-file environments/.env.metal --port 8081 --host 0.0.0.0
```
Hit Ctrl+C once you see the startup log with no tracebacks — this confirms `get_image_service`'s updated construction wires up cleanly against the real `AsyncSessionLocal`.

- [ ] **Step 10: Commit**

```bash
git add Backend/app/repositories/duplicate_decisions_repository.py Backend/app/repositories/image_repository.py Backend/app/services/image_service.py Backend/tests/test_image_service.py
git commit -m "feat: add duplicate-decision repository and ImageService methods"
```

---

### Task 5: Backend API endpoints

**Files:**
- Modify: `Backend/app/api/images.py` (Pydantic models + 3 routes)
- Modify: `backend_api.md`
- Modify: `Backend/tests/test_images_endpoints.py`

**Interfaces:**
- Consumes: `ImageService.dismiss_cluster`, `.undo_dismiss`, `.list_duplicate_decisions` (Task 4).
- Produces:
  - `POST /api/images/duplicates/clusters/{cluster_id}/dismiss` → `{"pairs": [{"image_id1": str, "image_id2": str}, ...]}`, 404 if cluster not found.
  - `POST /api/images/duplicates/pairs/undo-dismiss` body `{"pairs": [...]}` → 200, no content.
  - `GET /api/images/duplicates/decisions?limit=&offset=` → `{"items": [{"image_id1", "filename1", "image_id2", "filename2", "decided_at"}, ...], "total": int}`.

- [ ] **Step 1: Write the failing endpoint tests**

Add to `Backend/tests/test_images_endpoints.py`. First update the top imports:
```python
import uuid
from datetime import datetime, timezone
```
(add alongside the existing `import pytest` etc.) Then append these test classes at the end of the file:

```python
class TestDismissDuplicateCluster:
    """Tests for POST /api/images/duplicates/clusters/{cluster_id}/dismiss."""

    def test_dismiss_success(self, client, mock_image_service):
        mock_image_service.dismiss_cluster.return_value = [
            (uuid.UUID("11111111-1111-1111-1111-111111111111"),
             uuid.UUID("22222222-2222-2222-2222-222222222222")),
        ]

        response = client.post("/api/images/duplicates/clusters/141/dismiss")

        assert response.status_code == 200
        data = response.json()
        assert data["pairs"] == [{
            "image_id1": "11111111-1111-1111-1111-111111111111",
            "image_id2": "22222222-2222-2222-2222-222222222222",
        }]
        mock_image_service.dismiss_cluster.assert_called_once_with(141)

    def test_dismiss_not_found(self, client, mock_image_service):
        from fastapi import HTTPException
        mock_image_service.dismiss_cluster.side_effect = HTTPException(
            status_code=404, detail="Cluster 999 not found"
        )

        response = client.post("/api/images/duplicates/clusters/999/dismiss")

        assert response.status_code == 404


class TestUndoDismissDuplicates:
    """Tests for POST /api/images/duplicates/pairs/undo-dismiss."""

    def test_undo_success(self, client, mock_image_service):
        mock_image_service.undo_dismiss.return_value = None

        response = client.post(
            "/api/images/duplicates/pairs/undo-dismiss",
            json={"pairs": [{
                "image_id1": "11111111-1111-1111-1111-111111111111",
                "image_id2": "22222222-2222-2222-2222-222222222222",
            }]},
        )

        assert response.status_code == 200
        mock_image_service.undo_dismiss.assert_called_once()
        called_pairs = mock_image_service.undo_dismiss.call_args.args[0]
        assert called_pairs == [(
            uuid.UUID("11111111-1111-1111-1111-111111111111"),
            uuid.UUID("22222222-2222-2222-2222-222222222222"),
        )]


class TestListDuplicateDecisions:
    """Tests for GET /api/images/duplicates/decisions."""

    def test_list_success(self, client, mock_image_service):
        mock_image_service.list_duplicate_decisions.return_value = (
            [(uuid.UUID("11111111-1111-1111-1111-111111111111"), "a.jpg",
              uuid.UUID("22222222-2222-2222-2222-222222222222"), "b.jpg",
              datetime(2026, 8, 19, tzinfo=timezone.utc))],
            1,
        )

        response = client.get("/api/images/duplicates/decisions")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["filename1"] == "a.jpg"
        mock_image_service.list_duplicate_decisions.assert_called_once_with(limit=20, offset=0)

    def test_list_with_pagination_params(self, client, mock_image_service):
        mock_image_service.list_duplicate_decisions.return_value = ([], 0)

        response = client.get("/api/images/duplicates/decisions", params={"limit": 5, "offset": 10})

        assert response.status_code == 200
        mock_image_service.list_duplicate_decisions.assert_called_once_with(limit=5, offset=10)
```

Also update the file's top docstring (lines 3-11) to add the three new endpoints to the list, matching its existing style.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd Backend && pytest tests/test_images_endpoints.py -v -k "Dismiss or Undo or ListDuplicateDecisions"
```

Expected: 404s / failures — the routes don't exist yet.

- [ ] **Step 3: Add the Pydantic models and routes**

In `Backend/app/api/images.py`:

1. Add imports at the top: `import uuid` and `from datetime import datetime` (add alongside existing imports — check they're not already present first).
2. Add `from pydantic import BaseModel` (check it's not already imported).
3. Right after `router = APIRouter(prefix="/images", tags=["images"])`, add:

```python
class DuplicatePairModel(BaseModel):
    image_id1: str
    image_id2: str


class DuplicateDismissResponseModel(BaseModel):
    pairs: list[DuplicatePairModel]


class DuplicateUndoDismissRequest(BaseModel):
    pairs: list[DuplicatePairModel]


class DuplicateDecisionItemModel(BaseModel):
    image_id1: str
    filename1: str
    image_id2: str
    filename2: str
    decided_at: datetime


class DuplicateDecisionListResponseModel(BaseModel):
    items: list[DuplicateDecisionItemModel]
    total: int
```

4. Right after the existing `get_duplicate_images` route (the `GET /duplicates` endpoint, ends with `return await service.get_duplicates_clustered(...)`), add:

```python
@router.post("/duplicates/clusters/{cluster_id}/dismiss", response_model=DuplicateDismissResponseModel)
async def dismiss_duplicate_cluster(
    cluster_id: int,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    pairs = await service.dismiss_cluster(cluster_id)
    return DuplicateDismissResponseModel(
        pairs=[DuplicatePairModel(image_id1=str(a), image_id2=str(b)) for a, b in pairs]
    )


@router.post("/duplicates/pairs/undo-dismiss")
async def undo_dismiss_duplicates(
    body: DuplicateUndoDismissRequest,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    pairs = [(uuid.UUID(p.image_id1), uuid.UUID(p.image_id2)) for p in body.pairs]
    await service.undo_dismiss(pairs)


@router.get("/duplicates/decisions", response_model=DuplicateDecisionListResponseModel)
async def get_duplicate_decisions(
    response: Response,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    items, total = await service.list_duplicate_decisions(limit=limit, offset=offset)
    return DuplicateDecisionListResponseModel(
        items=[
            DuplicateDecisionItemModel(
                image_id1=str(row[0]), filename1=row[1],
                image_id2=str(row[2]), filename2=row[3],
                decided_at=row[4],
            )
            for row in items
        ],
        total=total,
    )
```

The file's later `# Must be before /{image_id} endpoint` ordering rule doesn't apply to these three routes — none of them collide with the single-segment `/{image_id}` pattern registered further down, since each has more path segments. Group them with the existing `/duplicates` route for readability, as shown.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd Backend && pytest tests/test_images_endpoints.py -v
```

Expected: all tests PASS, including every pre-existing test in the file (confirming nothing else broke).

- [ ] **Step 5: Update `backend_api.md`**

Insert this new content immediately after the existing "Get Duplicate Images" section (ends with `- **Example (backward)**: ...`) and before "Get Image File":

```markdown
#### Dismiss Duplicate Cluster

Record that every pair of images in a duplicate cluster is confirmed **not** a duplicate. Looks
up the cluster's current members server-side (never client-supplied) and records all `C(N, 2)`
pairs. Does not trigger a `clusterize` rerun — the effect is visible the next time `clusterize`
runs (manually, via `/admin/batches`). See
`docs/superpowers/specs/2026-08-19-duplicate-dismissal-decisions-design.md`.

- **URL**: `/api/images/duplicates/clusters/{cluster_id}/dismiss`
- **Method**: `POST`
- **Path Parameters**:
  - `cluster_id`: The cluster's `clusterId` as returned by `GET /api/images/duplicates`
- **Response**: `{"pairs": [{"image_id1": "...", "image_id2": "..."}, ...]}` — every pair recorded
- **Errors**: `404` if `cluster_id` doesn't currently exist in `tmp_clusters`
- **Cache**: no-cache
- **Example**: `POST /api/images/duplicates/clusters/141/dismiss`

#### Undo Dismiss Duplicates

Delete previously-recorded not-duplicate decisions for the given pairs, so they can be
re-clustered again on the next `clusterize` run.

- **URL**: `/api/images/duplicates/pairs/undo-dismiss`
- **Method**: `POST`
- **Body**: `{"pairs": [{"image_id1": "...", "image_id2": "..."}, ...]}`
- **Response**: Success (no content)
- **Cache**: no-cache
- **Example**: `POST /api/images/duplicates/pairs/undo-dismiss` with body `{"pairs": [{"image_id1": "abc", "image_id2": "def"}]}`

#### List Duplicate Decisions

Recent not-duplicate decisions, newest first — for undoing a decision from a past session.

- **URL**: `/api/images/duplicates/decisions`
- **Method**: `GET`
- **Query Parameters**:
  - `limit` (optional): Number of results (1-100, default: 20)
  - `offset` (optional): Number of results to skip (default: 0)
- **Response**: `{"items": [{"image_id1", "filename1", "image_id2", "filename2", "decided_at"}, ...], "total": N}`
- **Cache**: no-cache
- **Example**: `GET /api/images/duplicates/decisions?limit=10`
```

- [ ] **Step 6: Commit**

```bash
git add Backend/app/api/images.py Backend/tests/test_images_endpoints.py backend_api.md
git commit -m "feat: add dismiss/undo-dismiss/list-decisions duplicate endpoints"
```

---

### Task 6: Frontend API client

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts`

**Interfaces:**
- Consumes: `DuplicatePair`, `DuplicateDismissResponse`, `DuplicateDecisionListResponse` (Task 3, from `../types/generated/all`).
- Produces (used by Task 7/8): `MemesApi.dismissDuplicateCluster(clusterId: number): Promise<DuplicateDismissResponse>`, `MemesApi.undoDismissDuplicates(pairs: DuplicatePair[]): Promise<void>`, `MemesApi.listDuplicateDecisions(limit?: number, offset?: number): Promise<DuplicateDecisionListResponse>`.

- [ ] **Step 1: Add the interface methods**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, add `DuplicatePair, DuplicateDismissResponse, DuplicateDecisionListResponse` to the existing `import type { ... } from "../types/generated/all"` block, and add to the `MemesApi` interface (near `iterateDuplicates`):

```ts
  dismissDuplicateCluster(clusterId: number): Promise<DuplicateDismissResponse>;

  undoDismissDuplicates(pairs: DuplicatePair[]): Promise<void>;

  listDuplicateDecisions(limit?: number, offset?: number): Promise<DuplicateDecisionListResponse>;
```

- [ ] **Step 2: Implement in `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, add the same types to its `import type { ... }` block, then add (near `resolveIngestionCluster`/`undoIngestionReject`):

```ts
  async dismissDuplicateCluster(clusterId: number): Promise<DuplicateDismissResponse> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/clusters/${clusterId}/dismiss`, {
      method: "POST",
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to dismiss cluster ${clusterId}: ${res.status}`)
    return res.json()
  }

  async undoDismissDuplicates(pairs: DuplicatePair[]): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/pairs/undo-dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ pairs }),
    })
    if (!res.ok) throw new Error(`Failed to undo dismiss: ${res.status}`)
  }

  async listDuplicateDecisions(limit = 20, offset = 0): Promise<DuplicateDecisionListResponse> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/decisions?${params.toString()}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to list duplicate decisions: ${res.status}`)
    return res.json()
  }
```

- [ ] **Step 3: Add mock defaults**

In `Frontend/memes-frontend/src/test/mockApi.ts`, add inside the returned object (near `iterateDuplicates`):

```ts
    dismissDuplicateCluster: vi.fn().mockResolvedValue({ pairs: [] }),
    undoDismissDuplicates: vi.fn().mockResolvedValue(undefined),
    listDuplicateDecisions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
```

- [ ] **Step 4: Type-check**

```bash
cd Frontend/memes-frontend
tsc -b
```

Expected: no errors — this is the verification gate for this task (a pure interface/client addition has no behavior of its own to unit-test beyond type correctness; Tasks 7/8 exercise these methods through component tests).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts Frontend/memes-frontend/src/test/mockApi.ts
git commit -m "feat: add duplicate-decision methods to the frontend API client"
```

---

### Task 7: "Not duplicates" action in the review list

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`
- Modify: `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx`

**Interfaces:**
- Consumes: `MemesApi.dismissDuplicateCluster`, `.undoDismissDuplicates` (Task 6).

- [ ] **Step 1: Write the failing tests**

Add to `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx` (uses the file's existing `clusterMeme` helper and `makeMockApi`):

```tsx
describe('MemesDuplicatesList dismiss/undo', () => {
  it('dismisses a cluster and shows an undo toast', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1)],
        facets: [], hasNext: false,
      }),
      dismissDuplicateCluster: vi.fn().mockResolvedValue({
        pairs: [{ image_id1: 'a', image_id2: 'b' }],
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    const button = await screen.findByRole('button', { name: 'Not duplicates' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(api.dismissDuplicateCluster).toHaveBeenCalledWith(1)
    })
    expect(await screen.findByText('Marked as not duplicates')).toBeInTheDocument()
    expect(screen.getByText(/Marked 2 images as not duplicates/)).toBeInTheDocument()
  })

  it('undoes a dismissal and restores the row', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1)],
        facets: [], hasNext: false,
      }),
      dismissDuplicateCluster: vi.fn().mockResolvedValue({
        pairs: [{ image_id1: 'a', image_id2: 'b' }],
      }),
      undoDismissDuplicates: vi.fn().mockResolvedValue(undefined),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Not duplicates' }))
    await screen.findByText('Marked as not duplicates')

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))

    await waitFor(() => {
      expect(api.undoDismissDuplicates).toHaveBeenCalledWith([{ image_id1: 'a', image_id2: 'b' }])
    })
    expect(await screen.findByRole('button', { name: 'Not duplicates' })).toBeInTheDocument()
  })
})
```

Add `fireEvent` to the file's existing `import { act, render, screen, waitFor } from '@testing-library/react'` line.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd Frontend/memes-frontend
vitest run src/components/MemesDuplicatesList.test.tsx
```

Expected: FAIL — no "Not duplicates" button exists yet.

- [ ] **Step 3: Implement the dismiss button, optimistic state, and undo toast**

In `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`:

1. Add `DuplicatePair` to the existing `import type { Meme } from "../types/generated/all"` line (`import type { Meme, DuplicatePair } from "../types/generated/all"`).

2. Add state, right after the existing `const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)` line:

```ts
  const [dismissedClusterIds, setDismissedClusterIds] = useState<Set<number>>(new Set())
  const [toast, setToast] = useState<{ clusterId: number; pairs: DuplicatePair[]; message: string } | null>(null)
```

3. Add handlers, near the other `handle*` functions (e.g. after `handleRangeChanged`'s definition):

```ts
  const handleDismiss = useCallback(async (clusterId: number, memberCount: number) => {
    try {
      const response = await memesApi.dismissDuplicateCluster(clusterId)
      setDismissedClusterIds(prev => new Set(prev).add(clusterId))
      setToast({
        clusterId,
        pairs: response.pairs,
        message: `Marked ${memberCount} images as not duplicates`,
      })
    } catch {
      // Left silent -- a failed dismiss just leaves the row showing as before, no
      // separate error UI for this first version.
    }
  }, [memesApi])

  const handleUndo = useCallback(async () => {
    if (!toast) return
    const { clusterId, pairs } = toast
    await memesApi.undoDismissDuplicates(pairs)
    setDismissedClusterIds(prev => {
      const next = new Set(prev)
      next.delete(clusterId)
      return next
    })
    setToast(null)
  }, [memesApi, toast])
```

4. Replace the `itemContent` prop of the `<Virtuoso>` element:

```tsx
        itemContent={(_index, row) => {
          const isDismissed = typeof row.clusterId === "number" && dismissedClusterIds.has(row.clusterId)
          if (isDismissed) {
            return (
              <div>
                <p className="py-4 text-sm text-gray-400 italic">Marked as not duplicates</p>
                <hr className="my-4 border-gray-300" />
              </div>
            )
          }
          return (
            <div>
              <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                {row.members.map(meme => (
                  <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
                ))}
              </div>
              {typeof row.clusterId === "number" && (
                <button
                  className="mt-2 text-xs rounded bg-gray-100 px-3 py-1 hover:bg-gray-200"
                  onClick={() => handleDismiss(row.clusterId as number, row.members.length)}
                >
                  Not duplicates
                </button>
              )}
              <hr className="my-4 border-gray-300" />
            </div>
          )
        }}
```

5. Add the toast, right before the closing `</div>` of the component's returned JSX (after the `{clusterRows.length === 0 && ...}` block):

```tsx
      {toast && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 bg-gray-900 text-white text-sm rounded px-4 py-2 shadow-lg flex items-center gap-3 z-50">
          <span>{toast.message}</span>
          <button className="underline" onClick={handleUndo}>Undo</button>
        </div>
      )}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd Frontend/memes-frontend
vitest run src/components/MemesDuplicatesList.test.tsx
```

Expected: all tests PASS, including every pre-existing test in the file (confirming the windowing/scroll-jump logic — which this task does not touch — still works).

- [ ] **Step 5: Type-check and lint**

```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
```

Expected: both clean (0 errors, 0 warnings on eslint per this repo's `--max-warnings 0` CI gate).

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx
git commit -m "feat: add Not duplicates dismiss action with undo toast to the review list"
```

---

### Task 8: Minimal decisions-audit panel in the admin page

**Files:**
- Create: `Frontend/memes-frontend/src/components/DuplicateDecisionsPanel.tsx`
- Create: `Frontend/memes-frontend/src/components/DuplicateDecisionsPanel.test.tsx`
- Modify: `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`

**Interfaces:**
- Consumes: `MemesApi.listDuplicateDecisions`, `.undoDismissDuplicates` (Task 6).
- Produces: `DuplicateDecisionsPanel({ memesApi }: { memesApi: MemesApi })` — a self-contained React component with its own pagination, rendered by `AdminBatchesPage`.

- [ ] **Step 1: Write the failing test**

Create `Frontend/memes-frontend/src/components/DuplicateDecisionsPanel.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import DuplicateDecisionsPanel from './DuplicateDecisionsPanel'
import { makeMockApi } from '../test/mockApi'

describe('DuplicateDecisionsPanel', () => {
  it('lists decisions and undoes one on click', async () => {
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({
        items: [{
          image_id1: 'a', filename1: 'a.jpg',
          image_id2: 'b', filename2: 'b.jpg',
          decided_at: '2026-08-19T00:00:00Z',
        }],
        total: 1,
      }),
      undoDismissDuplicates: vi.fn().mockResolvedValue(undefined),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    expect(await screen.findByText('a.jpg')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))

    await waitFor(() => {
      expect(api.undoDismissDuplicates).toHaveBeenCalledWith([{ image_id1: 'a', image_id2: 'b' }])
    })
  })

  it('shows an empty state with no decisions', async () => {
    const api = makeMockApi({
      listDuplicateDecisions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    })
    render(<DuplicateDecisionsPanel memesApi={api} />)

    expect(await screen.findByText('No decisions yet.')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd Frontend/memes-frontend
vitest run src/components/DuplicateDecisionsPanel.test.tsx
```

Expected: FAIL — the component doesn't exist yet.

- [ ] **Step 3: Implement the component**

Create `Frontend/memes-frontend/src/components/DuplicateDecisionsPanel.tsx`:

```tsx
import { useCallback, useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { DuplicateDecisionItem } from "../types/generated/all"

type Props = { memesApi: MemesApi }

const PAGE_SIZE = 20

export default function DuplicateDecisionsPanel({ memesApi }: Props) {
  const [items, setItems] = useState<DuplicateDecisionItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [undoing, setUndoing] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    return memesApi.listDuplicateDecisions(PAGE_SIZE, page * PAGE_SIZE)
      .then(res => { setItems(res.items); setTotal(res.total) })
      .finally(() => setLoading(false))
  }, [memesApi, page])

  useEffect(() => {
    void (async () => {
      await Promise.resolve()
      load()
    })()
  }, [load])

  function handleUndo(item: DuplicateDecisionItem) {
    const key = `${item.image_id1}:${item.image_id2}`
    setUndoing(key)
    memesApi.undoDismissDuplicates([{ image_id1: item.image_id1, image_id2: item.image_id2 }])
      .then(load)
      .finally(() => setUndoing(null))
  }

  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <h2 className="text-lg font-semibold mb-2">Not-duplicate decisions</h2>
      {loading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-1">Image 1</th>
              <th>Image 2</th>
              <th>Decided</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => {
              const key = `${item.image_id1}:${item.image_id2}`
              return (
                <tr key={key} className="border-b last:border-0">
                  <td className="py-1">{item.filename1}</td>
                  <td>{item.filename2}</td>
                  <td>{item.decided_at}</td>
                  <td>
                    <button
                      className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-50"
                      disabled={undoing === key}
                      onClick={() => handleUndo(item)}
                    >
                      Undo
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      {!loading && items.length === 0 && <p className="text-sm text-gray-400 mt-2">No decisions yet.</p>}
      <div className="flex items-center gap-3 mt-3">
        <button
          className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
          disabled={page === 0}
          onClick={() => setPage(p => Math.max(0, p - 1))}
        >
          Prev
        </button>
        <span className="text-xs text-gray-500">Page {page + 1} of {maxPage + 1}</span>
        <button
          className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
          disabled={page >= maxPage}
          onClick={() => setPage(p => p + 1)}
        >
          Next
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Wire it into `AdminBatchesPage`**

In `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`:

1. Add the import: `import DuplicateDecisionsPanel from "../components/DuplicateDecisionsPanel"`
2. In the returned JSX, add `<DuplicateDecisionsPanel memesApi={memesApi} />` right after the closing `</div>` of the batch-trigger section (the `bg-white rounded-lg p-4 shadow-sm mb-6` div containing the `BatchRow`s) and before the runs-table section.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd Frontend/memes-frontend
vitest run src/components/DuplicateDecisionsPanel.test.tsx src/pages/AdminBatchesPage.test.tsx
```

Expected: all PASS — `AdminBatchesPage.test.tsx`'s existing tests still pass unchanged because `mockApi.ts` (Task 6) already provides a default `listDuplicateDecisions` mock, so the newly-rendered panel doesn't need any per-test setup to avoid erroring.

- [ ] **Step 6: Type-check and lint**

```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
```

Expected: both clean.

- [ ] **Step 7: Full frontend verification**

```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
vitest run
```

Expected: all clean/passing — this is the final full-suite check for the whole feature's frontend half.

- [ ] **Step 8: Commit**

```bash
git add Frontend/memes-frontend/src/components/DuplicateDecisionsPanel.tsx Frontend/memes-frontend/src/components/DuplicateDecisionsPanel.test.tsx Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx
git commit -m "feat: add not-duplicate decisions audit panel to the admin page"
```

---

## Final verification (after all 8 tasks)

- [ ] Backend: `cd Backend && pytest` (full mocked-DB suite)
- [ ] Batch unit tests: `pytest batch/tests/`
- [ ] Integration tests: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v` (full root — required whenever `tests/integration/` changed, per this repo's own gotcha about not scoping to just the one new file)
- [ ] Frontend: `cd Frontend/memes-frontend && tsc -b && eslint src/ && vitest run`
- [ ] Manual smoke test: start `metal` backend + frontend, open `/duplicates`, dismiss a cluster, confirm the toast appears and the row content changes; click Undo, confirm the row reverts; open `/admin`, confirm the new panel lists the decision (if any remain undone) and that Undo there works too.
- [ ] Confirm `/api/diagnostics/health` and `/api/images?limit=1` still respond normally (this repo's standard pre-commit smoke check for backend changes).
