# Precomputed Statistics Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the statistics page load instantly by serving `GET /api/diagnostics/statistics` from a precomputed single-row snapshot that is refreshed hourly, after every wrapped batch run, and on demand.

**Architecture:** A new `statistics_snapshots` table holds one JSONB row (`name='corpus'`). A shared service function runs the existing `DiagnosticsRepository.get_statistics()` query, builds the response payload and upserts the row. That one function is called by three callers: the new `batch/build_statistics.py` script (hourly scheduler job and admin "Refresh now"), a best-effort hook in `batch/run_wrapper.py` (after any other successful batch), and the endpoint's fallback path (no snapshot yet). The endpoint otherwise just reads the row and adds `computed_at`.

**Tech Stack:** Python 3.11, SQLAlchemy 2 async + asyncpg, PostgreSQL JSONB, Alembic, FastAPI/Pydantic v2, pytest, React + TypeScript + vitest.

**Spec:** `docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md` (read it in full first).

## Global Constraints

- Python target is **3.11**; use `.venv311` for backend and batch work.
- Repositories must **not** call `session.commit()`; `get_async_db` commits for the backend, batch code commits explicitly.
- ORM models live only in `Storage/models.py`.
- Metrics are unchanged: this work changes *when* statistics are computed, not *what* is counted.
- Hourly refresh: scheduler entry `interval_minutes: 60`, `max_runtime_minutes: 10`, `batch_run_kind: statistics`.
- Post-batch hook failures are logged and swallowed; they never change the parent batch's outcome or exit code. The hook is skipped when the script is `build_statistics` itself.
- `computed_at` is additive and nullable in the API (`{"type": ["string", "null"], "format": "date-time"}`); existing clients must keep working.
- `diagnostics.py` hand-writes its response models: `computed_at` must be added to that router's own `StatisticsResponse`, not only to the generated one.
- **Never bind the always-on environment ports** (see `environments/Environments.md`). Verify endpoints with FastAPI's `TestClient`, never `uvicorn`.
- **Never run `alembic upgrade` against a live metal/general/IT database** as part of this plan. Migration rollout is a user-run hand-off in the last task. Subagents must not be given the main `DATABASE_URL` of a live environment.
- Test roots have different `asyncio_mode` settings: run `Backend/tests`, `batch/tests`, and `tests/integration` as **separate** `pytest` commands, never combined.
- `tests/integration/` needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the command line (docker container `ocr-db`, throwaway DB `ocrdb_test`).
- Commit messages end with the attribution trailer lines the executing harness specifies for this session.
- Windows dev shell: commands below are Git Bash syntax; run from the repo root `H:\workspace_sandbox\memes` unless a `cd` is shown.

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `Storage/models.py` | modify | `StatisticsSnapshot` ORM model (+ `JSONB` import) |
| `Storage/alembic/versions/b7e2c4a91d30_add_statistics_snapshots_table.py` | create | migration creating the table |
| `repository/statistics_snapshots.py` | create | `StatisticsSnapshotsRepository.get` / `.upsert` |
| `Backend/app/services/statistics_snapshot_service.py` | create | `CORPUS_SNAPSHOT`, `statistics_payload(row)`, `refresh_corpus_snapshot(...)` |
| `shared/schemas/statisticsresponse.schema.json` | modify | add nullable `computed_at` |
| generated TS / Kotlin / Python types | regenerate | follow the schema change |
| `backend_api.md` | modify | document `computed_at` and the snapshot behavior |
| `Backend/app/api/diagnostics.py` | modify | snapshot read path + fallback, `computed_at` field |
| `batch/build_statistics.py` | create | `refresh_snapshot()` + `main(trigger, run_id)` |
| `environments/batch_registry.yaml` | modify | register `build_statistics` (admin-triggerable) |
| `environments/settings.yaml` | modify | hourly `scheduler.jobs` entry |
| `batch/run_wrapper.py` | modify | best-effort post-batch refresh hook |
| `Frontend/memes-frontend/src/pages/formatUpdatedAgo.ts` | create | pure "Updated N min ago" formatter |
| `Frontend/memes-frontend/src/pages/StatisticsPage.tsx` | modify | render the freshness line |
| `CLAUDE.md` | modify | batch pipeline entry for `build_statistics` |
| tests | create/modify | see each task |

---

### Task 1: Storage — model, migration, snapshot repository

**Files:**
- Modify: `docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md` (status line only)
- Modify: `Storage/models.py` (import line 10; append model at end of file)
- Create: `Storage/alembic/versions/b7e2c4a91d30_add_statistics_snapshots_table.py`
- Create: `repository/statistics_snapshots.py`
- Test: `tests/integration/test_statistics_snapshots_repository.py`

**Interfaces:**
- Produces:
  - `Storage.models.StatisticsSnapshot` with columns `name: str` (PK), `payload: dict`, `computed_at: datetime` (tz-aware), `duration_ms: int`.
  - `repository.statistics_snapshots.StatisticsSnapshotsRepository(session)` with
    `async get(self, name: str) -> StatisticsSnapshot | None` and
    `async upsert(self, name: str, payload: dict, computed_at: datetime, duration_ms: int) -> None`.

- [ ] **Step 1: Mark the spec as in implementation**

In `docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md`, change the status line under the title from `status: planned` to `status: implementation` (the `Plan:` line below it already links this file).

- [ ] **Step 2: Write the failing integration test**

Create `tests/integration/test_statistics_snapshots_repository.py`:

```python
"""
Integration tests for repository/statistics_snapshots.py.

Requires a live PostgreSQL instance -- see tests/integration/conftest.py.
"""
import uuid
from datetime import datetime, timezone

import pytest

from repository.statistics_snapshots import StatisticsSnapshotsRepository


def _unique_name() -> str:
    return f"test-{uuid.uuid4()}"


@pytest.mark.asyncio(loop_scope="session")
async def test_get_returns_none_when_no_snapshot_exists(db_session):
    repo = StatisticsSnapshotsRepository(db_session)

    assert await repo.get(_unique_name()) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_upsert_then_get_round_trips_all_fields(db_session):
    repo = StatisticsSnapshotsRepository(db_session)
    name = _unique_name()
    payload = {"memes": {"total": 5}, "content": {"tags": 9}, "trends": {"runs": 1}}
    computed_at = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)

    await repo.upsert(name, payload, computed_at, duration_ms=1234)
    row = await repo.get(name)

    assert row is not None
    assert row.name == name
    assert row.payload == payload
    assert row.computed_at == computed_at
    assert row.duration_ms == 1234


@pytest.mark.asyncio(loop_scope="session")
async def test_second_upsert_overwrites_the_single_row(db_session):
    repo = StatisticsSnapshotsRepository(db_session)
    name = _unique_name()
    first_at = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
    second_at = datetime(2026, 9, 29, 13, 0, 0, tzinfo=timezone.utc)

    await repo.upsert(name, {"v": 1}, first_at, duration_ms=10)
    await repo.get(name)  # load into the session's identity map: a stale-read regression must show up below
    await repo.upsert(name, {"v": 2}, second_at, duration_ms=20)
    row = await repo.get(name)

    assert row.payload == {"v": 2}
    assert row.computed_at == second_at
    assert row.duration_ms == 20
```

- [ ] **Step 3: Run the test to verify it fails**

Run:
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_statistics_snapshots_repository.py -v
```
Expected: collection ERROR `ModuleNotFoundError: No module named 'repository.statistics_snapshots'`. (If the DB connection itself fails, start the `ocr-db` docker container first.)

- [ ] **Step 4: Add the ORM model**

In `Storage/models.py` change line 10 from
```python
from sqlalchemy.dialects.postgresql import UUID
```
to
```python
from sqlalchemy.dialects.postgresql import JSONB, UUID
```

Append at the very end of `Storage/models.py`:

```python


class StatisticsSnapshot(Base):
    """Latest precomputed result of an expensive statistics query, one row per `name`
    (only 'corpus' today). See docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md."""
    __tablename__ = "statistics_snapshots"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
```

- [ ] **Step 5: Add the repository**

Create `repository/statistics_snapshots.py`:

```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import StatisticsSnapshot


class StatisticsSnapshotsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, name: str) -> StatisticsSnapshot | None:
        # populate_existing: upsert() writes with a Core statement, which bypasses the ORM
        # identity map -- without this a row loaded earlier in the same session would be
        # returned stale after an overwrite.
        result = await self._session.execute(
            select(StatisticsSnapshot)
            .where(StatisticsSnapshot.name == name)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def upsert(self, name: str, payload: dict, computed_at: datetime, duration_ms: int) -> None:
        stmt = insert(StatisticsSnapshot).values(
            name=name, payload=payload, computed_at=computed_at, duration_ms=duration_ms,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["name"],
            set_={
                "payload": stmt.excluded.payload,
                "computed_at": stmt.excluded.computed_at,
                "duration_ms": stmt.excluded.duration_ms,
            },
        )
        await self._session.execute(stmt)
```

- [ ] **Step 6: Run the test to verify it passes**

Run:
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_statistics_snapshots_repository.py -v
```
Expected: 3 passed.

- [ ] **Step 7: Write the Alembic migration**

Create `Storage/alembic/versions/b7e2c4a91d30_add_statistics_snapshots_table.py` (current single head is `29a039fa4457`):

```python
"""add statistics_snapshots table

Revision ID: b7e2c4a91d30
Revises: 29a039fa4457
Create Date: 2026-09-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7e2c4a91d30'
down_revision: Union[str, Sequence[str], None] = '29a039fa4457'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'statistics_snapshots',
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('name'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('statistics_snapshots')
```

- [ ] **Step 8: Verify the migration chain and round-trip on a scratch database**

Confirm there is exactly one head:
```bash
cd Storage && alembic heads
```
Expected: a single line `b7e2c4a91d30 (head)`.

Then exercise upgrade/downgrade against a **throwaway scratch database** (never a live environment; this uses the `ocr-db` test container only):
```bash
docker exec ocr-db psql -U ocr -d postgres -c "CREATE DATABASE ocrdb_migtest"
docker exec ocr-db psql -U ocr -d ocrdb_migtest -c "CREATE EXTENSION IF NOT EXISTS vector"
export DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_migtest" BASE_PATH=/tmp/test_images
cd .. && python - <<'EOF'
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from Storage.models import Base

async def go():
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.begin() as c:
        await c.run_sync(Base.metadata.create_all)
        await c.exec_driver_sql("DROP TABLE statistics_snapshots")  # simulate a DB one migration behind
    await e.dispose()

asyncio.run(go())
EOF
cd Storage
alembic stamp 29a039fa4457
alembic upgrade head
docker exec ocr-db psql -U ocr -d ocrdb_migtest -c "\d statistics_snapshots"
alembic downgrade -1
docker exec ocr-db psql -U ocr -d ocrdb_migtest -c "\dt statistics_snapshots"
alembic upgrade head
cd ..
docker exec ocr-db psql -U ocr -d postgres -c "DROP DATABASE ocrdb_migtest"
unset DATABASE_URL BASE_PATH
```
Expected: `\d` shows the four columns with `name` as primary key and `payload` of type `jsonb`; after `downgrade -1`, `\dt` reports "Did not find any relation"; the final `upgrade head` succeeds. If the `ocr-db` container is not running, say so in your report and skip this step; do not point it at any other database.

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md Storage/models.py Storage/alembic/versions/b7e2c4a91d30_add_statistics_snapshots_table.py repository/statistics_snapshots.py tests/integration/test_statistics_snapshots_repository.py
git commit -m "feat: add statistics_snapshots table and repository"
```

---

### Task 2: Service — payload mapping and snapshot refresh

**Files:**
- Create: `Backend/app/services/statistics_snapshot_service.py`
- Test: `Backend/tests/test_statistics_snapshot_service.py`

**Interfaces:**
- Consumes: `DiagnosticsRepository.get_statistics()` (returns a row with the attributes listed in `statistics_payload` below) and `StatisticsSnapshotsRepository.upsert(name, payload, computed_at, duration_ms)` from Task 1.
- Produces (in `Backend.app.services.statistics_snapshot_service`):
  - `CORPUS_SNAPSHOT: str = "corpus"`
  - `statistics_payload(row) -> dict` returning `{"memes": {...}, "content": {...}, "trends": {...}}` (JSON-serializable, same nested field names as the API's `StatisticsResponse`).
  - `@dataclass(frozen=True) class StatisticsSnapshotResult: payload: dict; computed_at: datetime`
  - `async refresh_corpus_snapshot(diagnostics_repo, snapshots_repo) -> StatisticsSnapshotResult`. It does not commit.

- [ ] **Step 1: Write the failing tests**

Create `Backend/tests/test_statistics_snapshot_service.py`:

```python
"""
Tests for Backend/app/services/statistics_snapshot_service.py -- pure mapping and
orchestration; both repositories are mocked.
"""
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Backend.app.services.statistics_snapshot_service import (
    CORPUS_SNAPSHOT,
    refresh_corpus_snapshot,
    statistics_payload,
)


def _fake_stats_row(**overrides):
    defaults = dict(
        total_memes=100, pending=6, rejected=2, with_embeddings=90, with_ocr=80, with_tags=70,
        without_tags=30, with_descriptions=60, with_concept_tags=40,
        flagged=5, duplicate_clusters=3,
        ocr_texts=200, tags=300, concepts=10, concept_image_sets=12,
        concept_images=150,
        tag_keys=8, tag_values=90,
        trends_runs=4, trend_sources=2,
        descriptions_approved=21, descriptions_rejected=3, descriptions_feedback_total=24,
        ocr_missing_text_heavy_classification=7, text_heavy_missing_embeddings=4, embeddings_missing_lemmas=2,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestStatisticsPayload:
    def test_maps_every_row_field_into_the_nested_api_shape(self):
        payload = statistics_payload(_fake_stats_row())

        assert payload == {
            "memes": {
                "total": 100, "pending": 6, "rejected": 2, "with_embeddings": 90, "with_ocr": 80,
                "with_tags": 70, "without_tags": 30, "with_descriptions": 60, "with_concept_tags": 40,
                "flagged": 5, "duplicate_clusters": 3,
                "ocr_missing_text_heavy_classification": 7, "text_heavy_missing_embeddings": 4,
                "embeddings_missing_lemmas": 2,
            },
            "content": {
                "ocr_texts": 200, "tags": 300, "tag_keys": 8, "tag_values": 90, "concepts": 10,
                "concept_image_sets": 12, "concept_images": 150,
                "descriptions_approved": 21, "descriptions_rejected": 3, "descriptions_feedback_total": 24,
            },
            "trends": {"runs": 4, "trend_sources": 2},
        }


class TestRefreshCorpusSnapshot:
    @pytest.mark.asyncio
    async def test_computes_upserts_and_returns_the_same_payload(self):
        diagnostics_repo = AsyncMock()
        diagnostics_repo.get_statistics.return_value = _fake_stats_row()
        snapshots_repo = AsyncMock()

        result = await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)

        assert result.payload == statistics_payload(_fake_stats_row())
        assert result.computed_at.tzinfo is not None
        assert result.computed_at.utcoffset() == timezone.utc.utcoffset(None)
        snapshots_repo.upsert.assert_awaited_once()
        args = snapshots_repo.upsert.await_args.args
        assert args[0] == CORPUS_SNAPSHOT
        assert args[1] == result.payload
        assert args[2] == result.computed_at
        assert isinstance(args[3], int) and args[3] >= 0

    @pytest.mark.asyncio
    async def test_compute_failure_propagates_and_writes_nothing(self):
        diagnostics_repo = AsyncMock()
        diagnostics_repo.get_statistics.side_effect = RuntimeError("db down")
        snapshots_repo = AsyncMock()

        with pytest.raises(RuntimeError, match="db down"):
            await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)

        snapshots_repo.upsert.assert_not_awaited()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Backend && pytest tests/test_statistics_snapshot_service.py -v`
Expected: collection ERROR `ModuleNotFoundError: No module named 'Backend.app.services.statistics_snapshot_service'`.

- [ ] **Step 3: Write the service**

Create `Backend/app/services/statistics_snapshot_service.py`:

```python
"""Builds and stores the precomputed statistics snapshot.

Single implementation shared by batch/build_statistics.py, the run_wrapper post-batch
hook, and the /api/diagnostics/statistics fallback path, so all three produce an identical
payload. See docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md.
"""
import time
from dataclasses import dataclass
from datetime import datetime, timezone

CORPUS_SNAPSHOT = "corpus"


@dataclass(frozen=True)
class StatisticsSnapshotResult:
    payload: dict
    computed_at: datetime


def statistics_payload(row) -> dict:
    """Maps DiagnosticsRepository.get_statistics()'s flat row into the nested
    StatisticsResponse shape (plain dict, JSON-serializable)."""
    return {
        "memes": {
            "total": row.total_memes,
            "pending": row.pending,
            "rejected": row.rejected,
            "with_embeddings": row.with_embeddings,
            "with_ocr": row.with_ocr,
            "with_tags": row.with_tags,
            "without_tags": row.without_tags,
            "with_descriptions": row.with_descriptions,
            "with_concept_tags": row.with_concept_tags,
            "flagged": row.flagged,
            "duplicate_clusters": row.duplicate_clusters,
            "ocr_missing_text_heavy_classification": row.ocr_missing_text_heavy_classification,
            "text_heavy_missing_embeddings": row.text_heavy_missing_embeddings,
            "embeddings_missing_lemmas": row.embeddings_missing_lemmas,
        },
        "content": {
            "ocr_texts": row.ocr_texts,
            "tags": row.tags,
            "tag_keys": row.tag_keys,
            "tag_values": row.tag_values,
            "concepts": row.concepts,
            "concept_image_sets": row.concept_image_sets,
            "concept_images": row.concept_images,
            "descriptions_approved": row.descriptions_approved,
            "descriptions_rejected": row.descriptions_rejected,
            "descriptions_feedback_total": row.descriptions_feedback_total,
        },
        "trends": {
            "runs": row.trends_runs,
            "trend_sources": row.trend_sources,
        },
    }


async def refresh_corpus_snapshot(diagnostics_repo, snapshots_repo) -> StatisticsSnapshotResult:
    """Runs the (slow) statistics query, upserts the 'corpus' snapshot, and returns what was
    stored. Does not commit -- the caller owns the session/commit. If the query raises,
    nothing is written and the previous snapshot stays in place."""
    started = time.perf_counter()
    row = await diagnostics_repo.get_statistics()
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = statistics_payload(row)
    computed_at = datetime.now(timezone.utc)
    await snapshots_repo.upsert(CORPUS_SNAPSHOT, payload, computed_at, duration_ms)
    return StatisticsSnapshotResult(payload=payload, computed_at=computed_at)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Backend && pytest tests/test_statistics_snapshot_service.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/services/statistics_snapshot_service.py Backend/tests/test_statistics_snapshot_service.py
git commit -m "feat: add statistics snapshot service"
```

---

### Task 3: API contract — schema, regenerated types, docs

**Files:**
- Modify: `shared/schemas/statisticsresponse.schema.json`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, Kotlin DTOs under `AndroidClient/`, `Backend/app/types/generated/statisticsresponse.py`
- Modify: `backend_api.md` (`StatisticsResponse` block near line 238; `#### Statistics` section near line 879)

**Interfaces:**
- Produces: `StatisticsResponse.computed_at?: string | null` in the generated TypeScript type (consumed by Task 7) and an optional `computed_at` on the generated Python model.

- [ ] **Step 1: Confirm the generated trees are clean before regenerating**

Run:
```bash
git status --short Frontend/memes-frontend/src/types AndroidClient Backend/app/types shared/schemas
```
Expected: no output. (If there is output, stop and report; the regeneration diff must contain only this change.)

- [ ] **Step 2: Add the field to the schema**

Replace the whole contents of `shared/schemas/statisticsresponse.schema.json` with:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "statisticsresponse.schema.json",
  "title": "StatisticsResponse",
  "type": "object",
  "properties": {
    "memes":   { "$ref": "./statisticsmemestats.schema.json" },
    "content": { "$ref": "./statisticscontentstats.schema.json" },
    "trends":  { "$ref": "./statisticstrendsstats.schema.json" },
    "computed_at": { "type": ["string", "null"], "format": "date-time", "description": "When this snapshot was computed (UTC); null when unknown." }
  },
  "required": ["memes", "content", "trends"]
}
```

- [ ] **Step 3: Regenerate all three type trees**

```bash
bash Frontend/generate-types.sh
python AndroidClient/scripts/generate_dtos.py
cd Backend
datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
cd ..
```
(The authoritative command is in `documents/generation.md`; use it if it differs.)

- [ ] **Step 4: Verify the diff contains only the expected change**

Run: `git diff --stat Frontend/memes-frontend/src/types AndroidClient Backend/app/types`
Expected: changes limited to the statistics response type in each tree (a `computed_at` line). Run `git diff` on each touched file and confirm. If the generator produced unrelated hunks (tool-version drift), revert those files with `git checkout -- <file>` **only for files whose entire diff is unrelated**, and report the drift.

- [ ] **Step 5: Update `backend_api.md`**

1. In the `### StatisticsResponse` JSON block (near line 238), after the `"trends": { ... }` object, add a comma and a final key:
   ```json
     "computed_at": "string | null (ISO 8601, UTC)"
   ```
2. In `#### Statistics` (near line 879), replace the sentence
   `Returns row counts across all major tables in a single SQL round-trip.`
   with
   ```
   Returns row counts across all major tables from a precomputed snapshot (table `statistics_snapshots`), refreshed hourly, after every wrapped batch run, and on demand via the `build_statistics` job in `/admin/batches`. `computed_at` says when the snapshot was computed. If no snapshot exists yet (fresh environment), the first request computes and stores one, then returns it.
   ```
3. In that section's example response, add `"computed_at": "2026-09-29T12:00:00Z"` as the final key after `trends`.

- [ ] **Step 6: Commit**

```bash
git add shared/schemas/statisticsresponse.schema.json Frontend/memes-frontend/src/types AndroidClient Backend/app/types backend_api.md
git commit -m "feat: add computed_at to StatisticsResponse contract"
```

---

### Task 4: Endpoint — read snapshot, fall back to live compute

**Files:**
- Modify: `Backend/app/api/diagnostics.py` (imports; `StatisticsResponse`; new dependency; `statistics` handler at lines 73-110)
- Modify: `Backend/tests/test_diagnostics_endpoints.py`

**Interfaces:**
- Consumes: `CORPUS_SNAPSHOT`, `statistics_payload`, `refresh_corpus_snapshot` (Task 2); `StatisticsSnapshotsRepository.get` returning a row with `.payload` and `.computed_at` (Task 1).
- Produces: `get_statistics_snapshots_repo` FastAPI dependency (tests override it) and `StatisticsResponse.computed_at: datetime | None = None`.

- [ ] **Step 1: Update the test fixtures and add the failing tests**

In `Backend/tests/test_diagnostics_endpoints.py`:

1. Extend the imports at the top:
```python
from datetime import datetime, timezone
```
and change
```python
from Backend.app.api.diagnostics import router as diagnostics_router
```
to
```python
from Backend.app.api.diagnostics import router as diagnostics_router
from Backend.app.services.statistics_snapshot_service import statistics_payload
```

2. Replace the `client` fixture and add a `mock_snapshots_repo` fixture (default: no snapshot exists, so the four existing statistics tests exercise the live-compute fallback path unchanged):

```python
@pytest.fixture
def mock_snapshots_repo():
    repo = AsyncMock()
    repo.get.return_value = None
    return repo


@pytest.fixture
def client(mock_diagnostics_repo, mock_snapshots_repo):
    async def override_get_diagnostics_repo():
        yield mock_diagnostics_repo

    async def override_get_snapshots_repo():
        yield mock_snapshots_repo

    from Backend.app.api.diagnostics import get_diagnostics_repo, get_statistics_snapshots_repo
    app.dependency_overrides[get_diagnostics_repo] = override_get_diagnostics_repo
    app.dependency_overrides[get_statistics_snapshots_repo] = override_get_snapshots_repo

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
```

3. Append these tests inside `class TestStatistics` (after the last existing test):

```python
    def test_serves_stored_snapshot_without_recomputing(self, client, mock_diagnostics_repo, mock_snapshots_repo):
        mock_snapshots_repo.get.return_value = SimpleNamespace(
            payload=statistics_payload(_fake_stats_row(total_memes=555)),
            computed_at=datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc),
        )

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["total"] == 555
        assert data["computed_at"].startswith("2026-09-29T12:00:00")
        mock_diagnostics_repo.get_statistics.assert_not_awaited()
        mock_snapshots_repo.upsert.assert_not_awaited()

    def test_without_snapshot_computes_live_stores_it_and_reports_computed_at(
        self, client, mock_diagnostics_repo, mock_snapshots_repo,
    ):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(total_memes=321)

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["total"] == 321
        assert data["computed_at"] is not None
        mock_snapshots_repo.upsert.assert_awaited_once()

    def test_unusable_stored_payload_is_recomputed_instead_of_failing(
        self, client, mock_diagnostics_repo, mock_snapshots_repo,
    ):
        # e.g. an older snapshot written before a new stat field existed
        mock_snapshots_repo.get.return_value = SimpleNamespace(
            payload={"memes": {"total": 1}},
            computed_at=datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc),
        )
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(total_memes=777)

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        assert response.json()["memes"]["total"] == 777
        mock_snapshots_repo.upsert.assert_awaited_once()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Backend && pytest tests/test_diagnostics_endpoints.py -v`
Expected: FAIL/ERROR: `ImportError: cannot import name 'get_statistics_snapshots_repo'`.

- [ ] **Step 3: Implement the endpoint change**

In `Backend/app/api/diagnostics.py`:

1. Replace the import block at the top (lines 1-8) with:
```python
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from Storage.db import AsyncSessionLocal, get_async_db
from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Backend.app.services.statistics_snapshot_service import CORPUS_SNAPSHOT, refresh_corpus_snapshot
from repository.statistics_snapshots import StatisticsSnapshotsRepository
```

2. Add `computed_at` to the router's own `StatisticsResponse`:
```python
class StatisticsResponse(BaseModel):
    memes: MemeStats
    content: ContentStats
    trends: TrendsStats
    computed_at: datetime | None = None
```

3. Directly after `get_diagnostics_repo`, add:
```python
async def get_statistics_snapshots_repo(
    db: AsyncSessionLocal = Depends(get_async_db),
) -> AsyncGenerator[StatisticsSnapshotsRepository, None]:
    yield StatisticsSnapshotsRepository(db)
```

4. Replace the whole `statistics` handler (from `@router.get("/statistics", ...)` to the end of the file) with:
```python
@router.get("/statistics", response_model=StatisticsResponse)
async def statistics(
    diagnostics_repo: DiagnosticsRepository = Depends(get_diagnostics_repo),
    snapshots_repo: StatisticsSnapshotsRepository = Depends(get_statistics_snapshots_repo),
):
    """Serves the precomputed snapshot (see batch/build_statistics.py). Falls back to a live
    compute-and-store when there is no snapshot yet, or the stored payload no longer matches
    the response shape (e.g. written before a stat field was added)."""
    snapshot = await snapshots_repo.get(CORPUS_SNAPSHOT)
    if snapshot is not None:
        try:
            return StatisticsResponse(**snapshot.payload, computed_at=snapshot.computed_at)
        except ValidationError:
            pass  # fall through to a fresh compute, which also overwrites the unusable row
    result = await refresh_corpus_snapshot(diagnostics_repo, snapshots_repo)
    return StatisticsResponse(**result.payload, computed_at=result.computed_at)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Backend && pytest tests/test_diagnostics_endpoints.py -v`
Expected: all pass (the 4 pre-existing statistics tests via the fallback path, the 3 new ones, and the health tests).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/api/diagnostics.py Backend/tests/test_diagnostics_endpoints.py
git commit -m "feat: serve statistics from precomputed snapshot"
```

---

### Task 5: Batch script, registry entry, hourly scheduler job

**Files:**
- Create: `batch/build_statistics.py`
- Modify: `environments/batch_registry.yaml` (append)
- Modify: `environments/settings.yaml` (`scheduler.jobs`, after the `trends_batch` entry)
- Test: `batch/tests/test_build_statistics.py`

**Interfaces:**
- Consumes: `refresh_corpus_snapshot` (Task 2), `StatisticsSnapshotsRepository` (Task 1), `DiagnosticsRepository`.
- Produces:
  - `batch.build_statistics.refresh_snapshot() -> None` (async, own session, commits; used by Task 6's hook).
  - `batch.build_statistics.main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None`, the standard admin/scheduler contract.
  - Registry name `build_statistics` (kind `statistics`) and a scheduler job of the same name.

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_build_statistics.py`:

```python
"""
Unit tests for batch/build_statistics.py: main()'s self-tracking contract (matching
test_build_tags_from_ocr.py's TestMain style), refresh_snapshot()'s session wiring, and the
registry / scheduler config that makes the script runnable. No real DB.
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import batch.build_statistics as module
from batch.registry import BatchRegistry

REPO_ROOT = Path(__file__).parent.parent.parent


def _ctx(value):
    class _Ctx:
        async def __aenter__(self_inner):
            return value

        async def __aexit__(self_inner, *exc_info):
            return False

    return _Ctx()


class TestMain:
    @pytest.mark.asyncio
    async def test_self_tracked_path_uses_statistics_kind(self):
        refresh_mock = AsyncMock()

        with patch.object(module, "tracked_run", return_value=_ctx("run-1")) as tracked_run_mock, \
             patch.object(module, "refresh_snapshot", refresh_mock):
            await module.main(trigger="scheduled")

        tracked_run_mock.assert_called_once_with(kind="statistics", trigger="scheduled")
        refresh_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_pre_created_run_id_is_finished_not_recreated(self):
        refresh_mock = AsyncMock()

        with patch.object(module, "finish_existing_run", return_value=_ctx(None)) as finish_mock, \
             patch.object(module, "refresh_snapshot", refresh_mock):
            await module.main(trigger="manual", run_id="existing-run-1")

        finish_mock.assert_called_once_with("existing-run-1")
        refresh_mock.assert_awaited_once_with()


class TestRefreshSnapshot:
    @pytest.mark.asyncio
    async def test_runs_the_shared_refresh_in_one_session_and_commits(self):
        session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
        refresh_mock = AsyncMock()

        with patch.object(module, "AsyncSessionLocal", session_factory), \
             patch.object(module, "DiagnosticsRepository") as diagnostics_cls, \
             patch.object(module, "StatisticsSnapshotsRepository") as snapshots_cls, \
             patch.object(module, "refresh_corpus_snapshot", refresh_mock):
            await module.refresh_snapshot()

        diagnostics_cls.assert_called_once_with(session)
        snapshots_cls.assert_called_once_with(session)
        refresh_mock.assert_awaited_once_with(diagnostics_cls.return_value, snapshots_cls.return_value)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compute_failure_propagates_without_committing(self):
        session = AsyncMock()
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(module, "AsyncSessionLocal", session_factory), \
             patch.object(module, "DiagnosticsRepository"), \
             patch.object(module, "StatisticsSnapshotsRepository"), \
             patch.object(module, "refresh_corpus_snapshot", AsyncMock(side_effect=RuntimeError("db down"))):
            with pytest.raises(RuntimeError, match="db down"):
                await module.refresh_snapshot()

        session.commit.assert_not_awaited()


class TestRegistrationAndSchedule:
    def test_registered_as_admin_triggerable_script(self):
        registry = BatchRegistry(base_dir=REPO_ROOT / "environments")

        assert registry.get("build_statistics") == {
            "module": "batch.build_statistics",
            "kind": "statistics",
        }

    def test_scheduled_hourly_with_a_registered_script(self):
        with open(REPO_ROOT / "environments" / "settings.yaml", encoding="utf-8") as f:
            jobs = yaml.safe_load(f)["scheduler"]["jobs"]
        job = next(j for j in jobs if j["name"] == "build_statistics")

        assert job["script"] == "build_statistics"
        assert job["batch_run_kind"] == "statistics"
        assert job["interval_minutes"] == 60
        assert job["max_runtime_minutes"] == 10
        assert job.get("enabled", True) is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest batch/tests/test_build_statistics.py -v`
Expected: collection ERROR `ModuleNotFoundError: No module named 'batch.build_statistics'`.

- [ ] **Step 3: Write the batch script**

Create `batch/build_statistics.py`:

```python
"""Precomputes the statistics page's numbers into the statistics_snapshots table so
GET /api/diagnostics/statistics is a single-row read. Idempotent; safe to re-run anytime.

Runs hourly (scheduler.jobs), on demand from /admin/batches, and -- via refresh_snapshot()
-- best-effort after every other wrapped batch (batch/run_wrapper.py). See
docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md.
"""
import argparse
import asyncio
import uuid

from Backend.app.repositories.diagnostics_repository import DiagnosticsRepository
from Backend.app.services.statistics_snapshot_service import refresh_corpus_snapshot
from batch.run_tracking import finish_existing_run, tracked_run
from config.settings import load_env
from repository.statistics_snapshots import StatisticsSnapshotsRepository
from Storage.db import AsyncSessionLocal


async def refresh_snapshot() -> None:
    """Compute the 'corpus' snapshot and commit it. If the query fails nothing is committed
    and the previous snapshot stays in place (stale beats missing)."""
    async with AsyncSessionLocal() as session:
        await refresh_corpus_snapshot(
            DiagnosticsRepository(session),
            StatisticsSnapshotsRepository(session),
        )
        await session.commit()


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await refresh_snapshot()
    else:
        async with tracked_run(kind="statistics", trigger=trigger):
            await refresh_snapshot()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())
```

- [ ] **Step 4: Register the script**

Append to the end of `environments/batch_registry.yaml`:

```yaml
build_statistics:
  module: batch.build_statistics
  kind: statistics
```
(Make sure the previous last line ends with a newline first.)

- [ ] **Step 5: Add the hourly scheduler job**

In `environments/settings.yaml`, directly after the `trends_batch` job (the block ending `enabled: true` at line ~125) and before the `# ingest_auto_prep, build_tags_from_ocr, ...` comment, insert:

```yaml
    - name: build_statistics
      script: build_statistics
      batch_run_kind: statistics
      interval_minutes: 60
      max_runtime_minutes: 10
      enabled: true
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest batch/tests/test_build_statistics.py batch/tests/test_registry.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add batch/build_statistics.py batch/tests/test_build_statistics.py environments/batch_registry.yaml environments/settings.yaml
git commit -m "feat: add build_statistics batch job with hourly schedule"
```

---

### Task 6: Post-batch refresh hook in `run_wrapper`

**Files:**
- Modify: `batch/run_wrapper.py`
- Modify: `batch/tests/test_run_wrapper.py`

**Interfaces:**
- Consumes: `batch.build_statistics.refresh_snapshot` (Task 5).
- Produces: `batch.run_wrapper._refresh_statistics_best_effort() -> None` (async; never raises).

- [ ] **Step 1: Write the failing tests and neutralize the hook in the existing tests**

In `batch/tests/test_run_wrapper.py`:

1. After `import batch.run_wrapper as run_wrapper` add:
```python

# Captured at import time, before the autouse fixture below replaces the module attribute.
_REAL_REFRESH = run_wrapper._refresh_statistics_best_effort


@pytest.fixture(autouse=True)
def refresh_hook_mock():
    """Every test in this file runs the real main(); without this, the post-batch hook would
    try to reach a real database after each fake script."""
    with patch("batch.run_wrapper._refresh_statistics_best_effort", new=AsyncMock()) as mock:
        yield mock
```

2. Append these classes at the end of the file:

```python
def _run_argv(script: str) -> list[str]:
    return ["run_wrapper.py", "--script", script, "--env", "metal", "--trigger", "scheduled"]


def _registry_for(script: str) -> MagicMock:
    registry = MagicMock()
    registry.all_names.return_value = [script]
    registry.get.return_value = {"module": f"batch.{script}", "kind": script}
    return registry


class TestPostBatchStatisticsRefresh:
    @pytest.mark.asyncio
    async def test_refreshes_after_a_successful_script(self, refresh_hook_mock):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()

        with patch("batch.run_wrapper.BatchRegistry", return_value=_registry_for("trends_batch")), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", _run_argv("trends_batch")):
            await run_wrapper.main()

        refresh_hook_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_does_not_refresh_when_the_script_fails(self, refresh_hook_mock):
        fake_module = MagicMock()
        fake_module.main = AsyncMock(side_effect=RuntimeError("boom"))

        with patch("batch.run_wrapper.BatchRegistry", return_value=_registry_for("trends_batch")), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", _run_argv("trends_batch")):
            with pytest.raises(RuntimeError, match="boom"):
                await run_wrapper.main()

        refresh_hook_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_does_not_refresh_after_build_statistics_itself(self, refresh_hook_mock):
        fake_module = MagicMock()
        fake_module.main = AsyncMock()

        with patch("batch.run_wrapper.BatchRegistry", return_value=_registry_for("build_statistics")), \
             patch("batch.run_wrapper.load_env"), \
             patch("batch.run_wrapper.importlib.import_module", return_value=fake_module), \
             patch.object(sys, "argv", _run_argv("build_statistics")):
            await run_wrapper.main()

        refresh_hook_mock.assert_not_awaited()


class TestRefreshStatisticsBestEffort:
    @pytest.mark.asyncio
    async def test_calls_build_statistics_refresh_snapshot(self):
        with patch("batch.build_statistics.refresh_snapshot", new=AsyncMock()) as refresh_mock:
            await _REAL_REFRESH()

        refresh_mock.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_swallows_refresh_failures(self):
        with patch("batch.build_statistics.refresh_snapshot",
                   new=AsyncMock(side_effect=RuntimeError("db down"))):
            await _REAL_REFRESH()  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest batch/tests/test_run_wrapper.py -v`
Expected: FAIL/ERROR: `AttributeError: module 'batch.run_wrapper' has no attribute '_refresh_statistics_best_effort'`.

- [ ] **Step 3: Implement the hook**

Replace `batch/run_wrapper.py` with:

```python
import argparse
import asyncio
import importlib
import logging
import sys
import uuid

from batch.registry import BatchRegistry
from config.settings import load_env

logger = logging.getLogger(__name__)


async def _refresh_statistics_best_effort() -> None:
    """Refreshes the precomputed statistics snapshot after a successful batch. Best-effort by
    design: any failure (including an import error) is logged and swallowed so it can never
    change the parent batch's outcome or exit code. Scripts invoked directly from a shell
    bypass this wrapper; the hourly build_statistics job covers those. See
    docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md."""
    try:
        from batch.build_statistics import refresh_snapshot
        await refresh_snapshot()
    except Exception:
        logger.warning("post-batch statistics refresh failed", exc_info=True)


async def main() -> None:
    registry = BatchRegistry()
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", choices=registry.all_names(), required=True)
    parser.add_argument("--env", choices=["metal", "general", "it"], required=True)
    parser.add_argument("--trigger", choices=["manual", "scheduled"], required=True)
    parser.add_argument("--run-id", default=None,
                         help="Pre-created run id (admin controller); omitted for the scheduler, "
                              "which lets this wrapper create its own run.")
    args = parser.parse_args()
    load_env(args.env)

    entry = registry.get(args.script)  # re-read here too, not reused from the choices lookup above
    module = importlib.import_module(entry["module"])
    run_id = uuid.UUID(args.run_id) if args.run_id else None
    await module.main(trigger=args.trigger, run_id=run_id)

    if args.script != "build_statistics":
        await _refresh_statistics_best_effort()


if __name__ == "__main__":
    asyncio.run(main())
```

(The file originally imported `sys` without using it; keep that import as-is to keep the diff minimal.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest batch/tests/test_run_wrapper.py -v`
Expected: all pass (3 original + 5 new).

- [ ] **Step 5: Commit**

```bash
git add batch/run_wrapper.py batch/tests/test_run_wrapper.py
git commit -m "feat: refresh statistics snapshot after wrapped batch runs"
```

---

### Task 7: Frontend — "Updated N min ago"

**Files:**
- Create: `Frontend/memes-frontend/src/pages/formatUpdatedAgo.ts`
- Test: `Frontend/memes-frontend/src/pages/formatUpdatedAgo.test.ts`
- Modify: `Frontend/memes-frontend/src/pages/StatisticsPage.tsx`

**Interfaces:**
- Consumes: `StatisticsResponse.computed_at?: string | null` in `src/types/generated/all.d.ts` (regenerated in Task 3).
- Produces: `formatUpdatedAgo(computedAt: string | null | undefined, now?: number): string | null`.

All commands in this task run from `Frontend/memes-frontend/`.

- [ ] **Step 1: Write the failing test**

Create `src/pages/formatUpdatedAgo.test.ts`:

```ts
import { formatUpdatedAgo } from './formatUpdatedAgo'

const NOW = Date.parse('2026-09-29T12:00:00Z')

describe('formatUpdatedAgo', () => {
  it('returns null when there is no timestamp', () => {
    expect(formatUpdatedAgo(null, NOW)).toBeNull()
    expect(formatUpdatedAgo(undefined, NOW)).toBeNull()
  })

  it('returns null for an unparseable timestamp', () => {
    expect(formatUpdatedAgo('not a date', NOW)).toBeNull()
  })

  it('says "just now" under a minute, and for clock skew into the future', () => {
    expect(formatUpdatedAgo('2026-09-29T11:59:30Z', NOW)).toBe('Updated just now')
    expect(formatUpdatedAgo('2026-09-29T12:05:00Z', NOW)).toBe('Updated just now')
  })

  it('uses minutes under an hour', () => {
    expect(formatUpdatedAgo('2026-09-29T11:55:00Z', NOW)).toBe('Updated 5 min ago')
    expect(formatUpdatedAgo('2026-09-29T11:00:01Z', NOW)).toBe('Updated 59 min ago')
  })

  it('uses hours under a day', () => {
    expect(formatUpdatedAgo('2026-09-29T11:00:00Z', NOW)).toBe('Updated 1 h ago')
    expect(formatUpdatedAgo('2026-09-28T13:00:00Z', NOW)).toBe('Updated 23 h ago')
  })

  it('uses days beyond that', () => {
    expect(formatUpdatedAgo('2026-09-28T12:00:00Z', NOW)).toBe('Updated 1 d ago')
    expect(formatUpdatedAgo('2026-09-22T12:00:00Z', NOW)).toBe('Updated 7 d ago')
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec vitest run src/pages/formatUpdatedAgo.test.ts`
Expected: FAIL: cannot resolve `./formatUpdatedAgo`.

- [ ] **Step 3: Write the formatter**

Create `src/pages/formatUpdatedAgo.ts`:

```ts
export function formatUpdatedAgo(
  computedAt: string | null | undefined,
  now: number = Date.now(),
): string | null {
  if (!computedAt) return null
  const then = Date.parse(computedAt)
  if (Number.isNaN(then)) return null

  const minutes = Math.floor((now - then) / 60_000)
  if (minutes < 1) return 'Updated just now'
  if (minutes < 60) return `Updated ${minutes} min ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `Updated ${hours} h ago`
  return `Updated ${Math.floor(hours / 24)} d ago`
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm exec vitest run src/pages/formatUpdatedAgo.test.ts`
Expected: 6 passed.

- [ ] **Step 5: Show it on the page**

In `src/pages/StatisticsPage.tsx`:

1. Add to the imports:
```tsx
import { formatUpdatedAgo } from "./formatUpdatedAgo"
```
2. Directly after `const { memes, content } = stats` add:
```tsx
  const updatedAgo = formatUpdatedAgo(stats.computed_at)
```
3. In the final (loaded-state) `return`, replace the heading that precedes `<section>` with the "Library" grid, i.e. change
```tsx
      <h1 className="text-2xl font-bold mb-4">Statistics</h1>

      <section>
        <h2 className="text-lg font-semibold mb-3">Library</h2>
```
to
```tsx
      <div>
        <h1 className="text-2xl font-bold mb-1">Statistics</h1>
        {updatedAgo && <p className="text-sm text-gray-500">{updatedAgo}</p>}
      </div>

      <section>
        <h2 className="text-lg font-semibold mb-3">Library</h2>
```
(Leave the error-state and loading-state headings untouched.)

- [ ] **Step 6: Run the full frontend gate**

```bash
pnpm exec tsc -b
pnpm exec eslint src/ --max-warnings 0
pnpm exec vitest run
```
Expected: all three succeed with no errors or warnings.

- [ ] **Step 7: Commit**

```bash
git add Frontend/memes-frontend/src/pages/formatUpdatedAgo.ts Frontend/memes-frontend/src/pages/formatUpdatedAgo.test.ts Frontend/memes-frontend/src/pages/StatisticsPage.tsx
git commit -m "feat: show statistics snapshot freshness on the statistics page"
```

---

### Task 8: Docs, full verification, review, hand-off

**Files:**
- Modify: `CLAUDE.md` (batch pipeline section)
- Create: `docs/superpowers/reviews/2026-09-29-precomputed-statistics-review.md`

- [ ] **Step 1: Document the new batch script in CLAUDE.md**

In `CLAUDE.md`, in the batch pipeline code block, find the line `# Concept discovery for the new rules engine (see Rules engine below)` and insert this entry **immediately before it** (after the `build_ocr_text_embeddings` entry, with one blank line separating):

```
build_statistics            → precomputes the statistics page's numbers into the single-row
                               statistics_snapshots table (name='corpus'), so GET
                               /api/diagnostics/statistics is a row read instead of ~28
                               subqueries. Scheduled hourly (scheduler.jobs), admin-triggerable
                               from /admin/batches, and also run best-effort by
                               batch/run_wrapper.py after every other successful wrapped batch
                               (failures there are logged and swallowed, and it is skipped for
                               build_statistics itself). Scripts run directly from a shell
                               bypass the wrapper, and inputs that change outside any batch
                               (flagged, description feedback, ingestion promote/reject) are
                               only picked up by the hourly run -- there is deliberately no
                               per-mutation invalidation. See
                               docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md.
```

- [ ] **Step 2: Run every affected test root, separately**

```bash
(cd Backend && pytest)
pytest batch/tests/
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all pass. The **entire** `tests/integration/` root is required, not just the new file, because `Storage/models.py` is shared code (see the CLAUDE.md gotcha on running the right test scope). If a failure looks unrelated, check whether it also fails on `main` (for example in a separate `git worktree`) before reporting; do not silently skip.

- [ ] **Step 3: Smoke-test the real app without binding a port**

Uses `TestClient` **without** a `with` block, so the app lifespan (and therefore the scheduler) never starts, against the throwaway `ocrdb_test` database only:

```bash
export DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" BASE_PATH=/tmp/test_images APP_ENV=general
python - <<'EOF'
import asyncio, os
from sqlalchemy.ext.asyncio import create_async_engine
from Storage.models import Base

async def go(action):
    e = create_async_engine(os.environ["DATABASE_URL"])
    async with e.begin() as c:
        await c.run_sync(getattr(Base.metadata, action))
    await e.dispose()

asyncio.run(go("create_all"))

from fastapi.testclient import TestClient
from Backend.app.main import app
c = TestClient(app)  # no `with`: lifespan/scheduler not started
print("health    ", c.get("/api/diagnostics/health").json())
first = c.get("/api/diagnostics/statistics").json()
second = c.get("/api/diagnostics/statistics").json()
print("first  computed_at:", first["computed_at"])
print("second computed_at:", second["computed_at"])
assert first["computed_at"] is not None and first["computed_at"] == second["computed_at"], "second call must be served from the stored snapshot"
print("images    ", c.get("/api/images?limit=1").status_code)

asyncio.run(go("drop_all"))
EOF
unset DATABASE_URL BASE_PATH APP_ENV
```
Expected: `health {'backend': True, 'database': True}`, identical `computed_at` on both statistics calls (the first computed and stored it, the second read it back), and `images 200`. This is the CLAUDE.md "smoke-test existing endpoints" requirement done without occupying any port.

- [ ] **Step 4: One review iteration**

Per CLAUDE.md's implementation workflow: review the full diff (`git diff main...HEAD`, or the commit range on the working branch) for logic correctness against the spec, code quality, and test coverage; confirm every spec requirement is addressed (storage, compute, three triggers, read path + fallback, contract/docs, frontend, tests). Save the report to `docs/superpowers/reviews/2026-09-29-precomputed-statistics-review.md` with sections: *What was fixed*, *Not fixed but explained*, *Intentionally ignored and why*. Fix any action points once, then stop (one iteration only).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/reviews/2026-09-29-precomputed-statistics-review.md
git commit -m "docs: document build_statistics and record review"
```

- [ ] **Step 6: Hand-off to the user (do not execute; report these steps)**

These touch the live, always-on environments and are for the user to run:

1. Before restarting anything: `pip check` in `.venv311` (venv-rot gotcha in CLAUDE.md).
2. Apply the migration to each live DB (from `Storage/`, per environment):
   ```powershell
   Get-Content ..\environments\.env.metal | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
   alembic upgrade head
   ```
   Repeat for `.env.general` and `.env.it`.
3. Restart each backend so the scheduler loads the new hourly job (`set WATCHFILES_FORCE_POLLING=1` first, per CLAUDE.md).
4. Optional: click **Refresh now** for `build_statistics` in `/admin/batches` on each environment to populate the first snapshot immediately; otherwise the first page load computes it via the fallback.
5. After merge, set the spec's status line to `done`.
