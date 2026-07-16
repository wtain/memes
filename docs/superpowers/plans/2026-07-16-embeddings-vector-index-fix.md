# Fix ineffective btree index on embeddings.embedding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `embeddings.embedding` column's ineffective plain-btree index (provides zero acceleration for the `cosine_distance()`/`<=>` queries it exists to support) with a proper pgvector `hnsw`/`vector_cosine_ops` index, matching the fix already applied to the sibling `ImageDescriptionEmbedding` table.

**Architecture:** Update `Storage/models.py`'s `Embedding` ORM model to declare the correct index (so fresh schemas built via `Base.metadata.create_all()`, e.g. in tests, match production), and add a new Alembic migration that rebuilds the index on real, populated databases using `CREATE INDEX CONCURRENTLY` inside an `autocommit_block()` (required because `CONCURRENTLY` cannot run inside Alembic's default per-migration transaction, and because this table is live and written to continuously by `build_image_embeddings.py`).

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, Alembic, pgvector 0.8.2, pytest.

**Full design reference:** `docs/superpowers/specs/2026-07-16-embeddings-vector-index-fix.md`.

**Explicitly out of scope for this plan** (per that spec, and confirmed with the human): actually running `alembic upgrade head` against the three real environments (`metal`, `general`, `it`). This plan only produces a correct, tested-offline migration ready to run. The live rollout — pre-flight row-count checks, `maintenance_work_mem` bump, `CREATE INDEX CONCURRENTLY` monitoring via `pg_stat_progress_create_index`, before/after `EXPLAIN ANALYZE` verification, one environment at a time — happens afterward, interactively, with explicit human confirmation before touching each real database. Do not attempt to run the migration against any real environment as part of this plan.

## Global Constraints

- Target Python is 3.11 (`.venv311`) — run all commands with that venv active.
- The new index must use `hnsw` with the `vector_cosine_ops` operator class — this must match the `cosine_distance()` operator `get_similar` actually calls; the default operator class (`vector_l2_ops`) would silently never be picked by the query planner for this query.
- `CREATE INDEX CONCURRENTLY`/`DROP INDEX CONCURRENTLY` must run inside `op.get_context().autocommit_block()`, not Alembic's default transactional `upgrade()` — Postgres rejects `CONCURRENTLY` inside a transaction block.
- No change to `get_duplicates`, `get_duplicates_precomputed`, `get_duplicates_clustered`, or `TmpDuplicates`/`TmpImageClusters` — none of their query shapes benefit from an ANN index on `embeddings.embedding`, and the spec explicitly scopes them out.
- This plan must not run the migration against any real `metal`/`general`/`it` database — offline verification (`--sql` render) only.

---

### Task 1: Fix the model and add the migration

**Files:**
- Modify: `Storage/models.py`
- Create: `Storage/alembic/versions/2026_07_16_fix_embeddings_hnsw_index.py`

**Interfaces:**
- Produces: `Embedding.embedding` (unchanged type, `Vector(EMBEDDING_DIM)`) now indexed via `hnsw`/`vector_cosine_ops` as `ix_embeddings_embedding_hnsw_cosine`, declared in `__table_args__` — replacing the old bare `index=True`. No behavior change to any existing method signature; `get_similar`'s query is unchanged code, just now backed by a working index.

- [ ] **Step 1: Update `Storage/models.py`**

Change:

```python
class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    embedding = Column(Vector(EMBEDDING_DIM), index=True)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="embeddings")
```

to:

```python
class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    embedding = Column(Vector(EMBEDDING_DIM))

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_embeddings_embedding_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    image = relationship("Image", back_populates="embeddings")
```

`Index` is already imported in `Storage/models.py` (used elsewhere in the file, e.g. `Image.__table_args__`) — no new import needed. This mirrors exactly how `ImageDescriptionEmbedding` was fixed earlier (same file).

- [ ] **Step 2: Create the migration**

Current Alembic head is `a3f9c1d8b6e2` (`2026_07_16_fix_image_description_embeddings_hnsw_index.py` — confirm this is still true by running `alembic heads` per Step 3 below before assuming it; if it's changed, use the actual current head as `down_revision` instead).

Create `Storage/alembic/versions/2026_07_16_fix_embeddings_hnsw_index.py`:

```python
"""fix embeddings.embedding index — btree provides no acceleration for cosine_distance

Revision ID: b4e1a9c7d3f2
Revises: a3f9c1d8b6e2
Create Date: 2026-07-16

"""
from alembic import op

revision = 'b4e1a9c7d3f2'
down_revision = 'a3f9c1d8b6e2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The original index (18ec91d67615_embeddings_index.py) was an unedited
    # autogenerated migration — SQLAlchemy's default index=True falls back
    # to a plain btree, which provides zero acceleration for pgvector's
    # cosine_distance() (<=>) operator used by get_similar's
    # ORDER BY ... LIMIT k query; it's dead weight on every write instead.
    # CONCURRENTLY avoids taking a blocking lock on this live, populated
    # table during the rebuild (build_image_embeddings.py writes to it
    # continuously) — which requires running outside Alembic's normal
    # per-migration transaction, hence autocommit_block().
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_embeddings_embedding_hnsw_cosine "
            "ON embeddings USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute("DROP INDEX CONCURRENTLY ix_embeddings_embedding")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_embeddings_embedding "
            "ON embeddings (embedding)"
        )
        op.execute("DROP INDEX CONCURRENTLY ix_embeddings_embedding_hnsw_cosine")
```

- [ ] **Step 3: Verify the migration offline (no live DB needed, and no real database is touched)**

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m alembic upgrade head --sql | tail -30
DATABASE_URL="postgresql+asyncpg://test:test@localhost/test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m alembic heads
cd ..
```

Expected: the rendered SQL includes `CREATE INDEX CONCURRENTLY ix_embeddings_embedding_hnsw_cosine ON embeddings USING hnsw (embedding vector_cosine_ops)` followed by `DROP INDEX CONCURRENTLY ix_embeddings_embedding`, with no traceback; `alembic heads` prints exactly one head, `b4e1a9c7d3f2 (head)`.

If the offline render errors specifically because of `autocommit_block()` in `--sql` mode (this repo has no prior example of using `autocommit_block()` — this is the first), do not work around it by guessing; report back what the actual error says (BLOCKED status) rather than silently changing the migration's mechanics — the human will decide how to proceed. Concretely: if this happens, do NOT drop `autocommit_block()` and fall back to a plain transactional `CREATE INDEX` (without `CONCURRENTLY`) without asking first — that changes the safety property (blocking lock during the real rollout) that this whole migration exists to preserve.

- [ ] **Step 4: Run the full test suite to confirm no regression**

The corrected index also affects how `tests/integration/conftest.py` builds its schema (`Base.metadata.create_all()`, from `Storage/models.py` directly, not from Alembic) — so the existing `Embedding`-using tests in `tests/integration/test_backend_image_repository.py` (`test_get_embedding_none_when_missing_and_vector_when_present`, `test_get_similar_excludes_self_and_orders_by_distance`) now exercise the corrected schema, not just the migration file. Run:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/ -q
H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest batch/tests/ tests/rules/ tests/ai/ -q
H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest Backend/tests/ -q
```

(Run each as a separate command — this repo's `Backend/tests/` and the other test roots have different `pytest.ini` `asyncio_mode` settings; combining them in one invocation breaks Backend's async test collection.)

Expected: all three commands report all tests passing, with no new failures compared to before this change (same counts as the pre-change baseline — the fix is index-only, no query/behavior change).

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/2026_07_16_fix_embeddings_hnsw_index.py
git commit -m "fix: replace ineffective btree index on embeddings.embedding with hnsw"
```

---

## After this plan is merged

Per the design spec's Rollout section — do this interactively, with the human, NOT as an unsupervised subagent task:

1. Pre-flight: `SELECT count(*) FROM embeddings;` against each of `metal` (port 5432), `general` (port 5434), `it` (port 5436) — confirm each Postgres instance's pgvector version supports `hnsw` (≥0.5.0) independently, don't assume from the one dev-environment check already done.
2. Apply to `metal`/`it` first if pre-flight confirms they're meaningfully smaller than `general`; apply to `general` last.
3. Before running: `SET maintenance_work_mem = '256MB';` (session-scoped, cheap insurance).
4. During the run: monitor `SELECT pid, phase, blocks_total, blocks_done, tuples_total, tuples_done FROM pg_stat_progress_create_index;` rather than assuming it finished.
5. After: confirm via `SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'embeddings';` that only `ix_embeddings_id`, `ix_embeddings_image_id`, and the new `ix_embeddings_embedding_hnsw_cosine` remain (old `ix_embeddings_embedding` gone), and run the spec's `EXPLAIN ANALYZE` before/after comparison to confirm the planner actually picks the new index for `get_similar`'s query shape.
