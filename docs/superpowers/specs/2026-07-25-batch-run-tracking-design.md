# Generic Batch Run Tracking — Design

**Date:** 2026-07-25
**Status:** Draft. Prerequisite for `2026-07-24-ingestion-pipeline-design.md` — ingestion needs a
run-tracking concept and this generalizes the one that already exists for trends instead of
inventing a second, ingestion-specific one.

---

## Motivation

`batch/trends_batch.py` already has a working run-tracking pattern: `TrendsRun` (one row per
execution, `status: started|completed|failed`) plus `TrendsRunResult` (per-run output rows), driven
by `TrendsRunRepository.create_run()` / `.commit()` / `.fail()`. It's small, it works, and nothing
else in the codebase has anything like it — every other batch job (`extract_text_from_memes`,
`build_image_embeddings`, `rebuild_duplicates`, ...) just prints progress to stdout and has no
durable record that a run happened, when, or whether it succeeded.

The ingestion pipeline (`2026-07-24-ingestion-pipeline-design.md`) needs exactly this shape of
thing — one row per ingestion run, a status, and a way to track which stage a run is in
(hash-dedup → in-batch review → cross-corpus review → promoted) — which its own draft had
provisionally called `ingestion_batches`. Building a second, near-identical, ingestion-only table
next to `trends_runs` would duplicate the concept rather than generalize it. This spec generalizes
`trends_runs` into a `batch_runs` table any batch job can use, and ingestion becomes its second
consumer (proving the abstraction actually generalizes, not just renaming for one caller).

## Scope

**In scope:** generalize the table/model/repository; migrate `trends_batch.py` (the only existing
consumer) onto it; ingestion's design doc gets updated to use it instead of a bespoke table.

**Out of scope:** retrofitting every other batch script (`extract_text_from_memes`,
`build_image_embeddings`, `build_tags_from_ocr`, etc.) to write `batch_runs` rows. Nothing about
this spec requires that, and doing it for every script is a separate, larger effort with its own
cost/benefit call — not a blocker for ingestion.

---

## Current shape (for reference)

```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

class TrendsRun(Base):
    __tablename__ = "trends_runs"
    run_id: UUID PK
    created_at: DateTime
    status: String(20)

class TrendsRunResult(Base):
    __tablename__ = "trends_run_results"
    id: BigInteger PK
    run_id: FK -> trends_runs.run_id (CASCADE)
    source_id: FK -> trend_sources.id (CASCADE)
    label, name, value
```

`TrendsRunRepository.create_run()` inserts a `started` row and flushes to get the id back before
the rest of the run does work; `.commit()`/`.fail()` flip status at the end. `trends_batch.py`
wraps its whole `main()` body in `try/except`, calling `fail()` on any exception and re-raising.

## What's missing for a second consumer

- **No `kind` discriminator** — today there's only one kind of run, so nothing distinguishes runs
  by job type. A shared table needs one.
- **No stage/progress tracking** — trends runs are single-shot (start → done), but ingestion is
  multi-stage (hash-dedup → in-batch review → cross-corpus review → promoted), and stages can span
  multiple separate process invocations (a human reviewing over hours or days between batch script
  runs). Need a place to persist "which stage is this run currently in."
- **No structured stats** — useful for both: trends could record source counts, ingestion needs
  per-stage counts (intake / hash-duplicates-removed / in-batch-duplicates-removed /
  cross-corpus-duplicates-removed / promoted).
- **No completion timestamp or failure reason** — `status` flips but there's no `completed_at` and
  `fail()` records no "why."
- **No "is one already running" query** — ingestion needs to guard against two concurrent ingestion
  runs targeting the same environment stepping on each other; trends never needed this because
  nothing else in the codebase triggers a second trends run mid-flight, but the primitive is
  generically useful.

## What's deliberately *not* added

- **No `environment` column.** metal/general/it are separate Postgres databases (see
  `environments/Environments.md`), not rows in a shared table — a `batch_runs` row already lives in
  exactly one environment's DB by construction. Adding an `environment` column would be redundant
  data that could drift from the DB it's actually stored in. (Same reasoning `TrendsRun` already
  follows today — it has no such column.)
- **No generic "chain of stage rows."** Modelling stage transitions as multiple linked `BatchRun`
  rows was considered and rejected as over-structured for what's needed — a single row with a
  mutable `stage` string plus a JSON `stats` blob covers both consumers without forcing trends
  (which has no stages) into an artificial multi-stage shape.

---

## Design

### Schema changes

Migration mirrors the existing `feed_sources` → `trend_sources` "generalize" migration
(`d4a1f7b2c9e6_generalize_trend_sources.py`): rename table, add nullable columns, backfill, then
tighten to `NOT NULL` where required. No data loss; `trends_run_results.run_id` keeps pointing at
the same (renamed) table with no need to touch that table.

```python
op.rename_table('trends_runs', 'batch_runs')

op.add_column('batch_runs', sa.Column('kind', sa.String(50), nullable=True))
op.add_column('batch_runs', sa.Column('stage', sa.String(50), nullable=True))
op.add_column('batch_runs', sa.Column('stats', sa.JSON(), nullable=True))
op.add_column('batch_runs', sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True))
op.add_column('batch_runs', sa.Column('error', sa.Text(), nullable=True))

op.execute("UPDATE batch_runs SET kind = 'trends' WHERE kind IS NULL")
op.alter_column('batch_runs', 'kind', nullable=False)
```

Resulting model (`Storage/models.py`):

```python
class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

    def __str__(self) -> str:
        return self.value


class BatchRun(Base):
    __tablename__ = "batch_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=str(RunStatus.started))
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`TrendsRun` is removed as a separate class; `TrendsRunResult.run_id`'s FK target becomes
`batch_runs.run_id`. `trend_sources`/`TrendsRunResult` are untouched otherwise.

### Repository

New `repository/batch_runs.py`, replacing `TrendsRunRepository` in `repository/trends.py` (which
keeps `TrendSourceRepository`/`TrendsRunResultRepository` — those stay trends-specific):

```python
class BatchRunRepository:
    def __init__(self, session): self._session = session

    async def create_run(self, kind: str, stage: str | None = None) -> uuid.UUID: ...
    async def set_stage(self, run_id: uuid.UUID, stage: str) -> None: ...
    async def commit(self, run_id: uuid.UUID, stats: dict | None = None) -> None: ...
    async def fail(self, run_id: uuid.UUID, error: str | None = None) -> None: ...
    async def get_active_run(self, kind: str) -> BatchRun | None:
        """Most recent row for `kind` with status == started, or None."""
    async def get_run(self, run_id: uuid.UUID) -> BatchRun | None: ...
```

`get_active_run(kind)` is the primitive ingestion uses to refuse starting a second concurrent batch
for the same environment (open question #8 in the ingestion draft) — since each environment is
already its own DB, "is there an active run of kind='ingestion'" is exactly "is there an active
ingestion run in *this* environment."

### `trends_batch.py` change

Only the import and repository name change — behavior identical:

```python
from repository.batch_runs import BatchRunRepository
...
runs_repo = BatchRunRepository(session)
run_id = await runs_repo.create_run(kind="trends")
...
await runs_repo.commit(run_id)   # stats param optional, unused for now
...
await runs_repo.fail(run_id)     # error param optional, unused for now
```

### How ingestion uses it

Replaces the `ingestion_batches` table proposed in the ingestion draft. One `batch_runs` row per
ingestion run, `kind="ingestion"`:

- `create_run(kind="ingestion", stage="hash_dedup")` at the start.
- `set_stage(run_id, "in_batch_review")`, `set_stage(run_id, "cross_corpus_review")`,
  `set_stage(run_id, "promoted")` as it progresses — each stage can span multiple separate process
  invocations (human review happens between script runs), so `stage` persists across them.
- `stats` accumulates per-stage counts as the run progresses (not just at the end) — e.g.
  `{"intake": 340, "hash_duplicates": 12, "in_batch_duplicates": 8, ...}`, updated via a small
  `update_stats(run_id, **kwargs)` helper that merges into the existing JSON rather than
  overwriting it wholesale.
- `images.ingestion_batch_id` (from the ingestion design) becomes a FK to `batch_runs.run_id`
  instead of a bespoke `ingestion_batches.id`.
- Before starting a new run, ingestion calls `get_active_run(kind="ingestion")` and refuses to
  start (or prompts to resume) if one is already `started`.

---

## Migration / rollout

1. Alembic revision: rename + add columns + backfill `kind='trends'`, per above.
2. Add `BatchRun` model, remove `TrendsRun`; keep `TrendsRunResult` pointed at the renamed table.
3. Add `repository/batch_runs.py`; remove `TrendsRunRepository` from `repository/trends.py`.
4. Update `trends_batch.py`'s two import/usage sites.
5. Update `tests/batch/test_trends_batch.py` for the renamed repository/model.
6. No downtime concerns — same reasoning as the `trend_sources` migration (additive columns +
   backfill, single rename). Each environment's DB migrates independently, as usual.

## Out of scope

- Adding `batch_runs` tracking to any batch script other than `trends_batch.py` and the ingestion
  pipeline being designed. A blanket rollout across all of `batch/` is a legitimate future idea but
  not required here and not free (every script would need its own meaningful `stats` shape).
- A UI for browsing batch run history — not needed until ingestion's own UI needs it, and out of
  scope for this prerequisite.
- Automatic cleanup/retention of old `batch_runs` rows — not addressed; volume is low (one row per
  trends run + one per ingestion run, not per-image), so not a near-term concern.
