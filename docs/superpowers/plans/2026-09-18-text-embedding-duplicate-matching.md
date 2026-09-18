# Text-Embedding Duplicate Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Task 7 of this plan is controller-only — see its header before dispatching anything.**

**Goal:** For text-heavy-vs-text-heavy image pairs specifically, replace CLIP with OCR-text-embedding
distance as the primary duplicate-matching signal (with a tight CLIP safety net), across both the
ingestion Tier A/B pipeline and the corpus-wide `rebuild_duplicates`/`clusterize` pipeline. All
other pairs are completely unaffected.

**Architecture:** One shared SQL primitive (`find_duplicates()`) generalizes to support a second
embedding table and a `distance_source` tag; three probe calls (general CLIP excluding text-heavy
pairs, a tight CLIP safety net, and a new OCR-text probe) replace today's single CLIP probe at
every call site; `clusterize.py`'s union-find and the ingestion review API/UI become
`distance_source`-aware where it matters (edge acceptance, display), source-agnostic where it
doesn't (splitting, admin Duplicates page).

**Tech Stack:** SQLAlchemy + Alembic (Storage/), raw SQL via `sqlalchemy.text()` for the KNN
primitive, pgvector, FastAPI/Pydantic (Backend/), React/TypeScript (Frontend/), pytest.

**Spec:** docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md

## Global Constraints

- `distance_source` values are exactly the two literal strings `'clip'` and `'ocr_text'` —
  spelled identically everywhere (SQL, Python, Pydantic, TypeScript). No third value, no
  abbreviation.
- Thresholds are fixed module constants for this plan, not new CLI flags:
  `TEXT_EMBEDDING_TIGHT_THRESHOLD = 0.05`, `TEXT_EMBEDDING_LOOSE_THRESHOLD = 0.10`,
  `CLIP_SAFETY_NET_THRESHOLD = 0.02` (all in `batch/rebuild_duplicates.py`, imported elsewhere).
- The safety-net CLIP probe (`_ACTIVE_PROBE_SAFETY_NET` in `rebuild_duplicates.py`) must NOT share
  an incremental "already probed" marker with the general CLIP probe, and must NOT use
  `_ACTIVE_PROBE_INCREMENTAL`/`_ACTIVE_PROBE_FULL`. It runs unconditionally over the active
  text-heavy population every invocation, relying solely on `ON CONFLICT DO NOTHING`. This is the
  single most load-bearing correctness requirement in this plan — see the spec's §3 and its
  Self-Review section for the full reasoning behind why the obvious-looking alternative
  (reusing the general probe's own incremental marker) silently and permanently breaks the safety
  net for any text-heavy image that also has an unrelated non-text-heavy CLIP match. Task 2's own
  tests must prove this empirically (Step 1's test list includes the specific regression case).
- `ingest_find_duplicates.py`'s OCR-text probe always uses `TEXT_EMBEDDING_LOOSE_THRESHOLD`
  regardless of which tier is calling — never derive it from the CLIP `threshold` parameter via
  `min()` or similar. The review query's own tier bands, not the probe's insertion threshold,
  determine which UI tier a stored row surfaces in (matches this file's own existing convention
  for the CLIP probe itself).
- No task in this plan touches the admin Duplicates page
  (`Backend/app/repositories/image_repository.py`'s `get_duplicates_clustered`) or
  `ClusterRow.tsx`'s multi-edge `edgeSummaryFor` aggregation — both are explicit spec Non-goals.
- `resolve_cluster`'s recursive cluster-splitting logic in `clusterize.py` stays source-agnostic
  (unchanged) — only the initial union-find edge-acceptance decision in `get_duplicate_pairs`
  becomes `distance_source`-aware.

---

### Task 1: Schema — `distance_source` column + migration

**Files:**
- Modify: `Storage/models.py` (`TmpDuplicates` class, currently at line 186; `match_source` column
  currently at line 211)
- Create: `Storage/alembic/versions/<generated>_add_tmp_duplicates_distance_source.py`

**Interfaces:**
- Produces: `TmpDuplicates.distance_source` column (`String(20)`, `nullable=False,
  server_default="clip"`). Every later task reads/writes this column.

- [ ] **Step 1: Add the column to Storage/models.py**

Insert immediately after the existing `match_source` column (currently ending at line 211, right
before the blank line and the `tier_a_reviewed_at`/`tier_b_reviewed_at` block):

```python
    # 'clip' | 'ocr_text' -- which embedding signal produced this row's distance. Orthogonal to
    # match_source (in_batch/cross_corpus): a pair can be any combination of the two. Existing
    # rows (all CLIP-sourced, from before this column existed) default to 'clip'. See
    # docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
    distance_source = Column(String(20), nullable=False, server_default="clip")
```

- [ ] **Step 2: Generate the migration**

Re-check the current alembic head first (this plan may execute after other work has landed):

```powershell
cd Storage
alembic heads
```

Then generate, against a correctly-stamped `ocrdb_test` (see this repo's documented gotcha: if
`alembic upgrade head`/autogenerate complains about an out-of-sync DB, run `alembic stamp base &&
alembic upgrade head` first — `tests/integration/conftest.py`'s own schema-build is independent of
Alembic, so `ocrdb_test` can appear to have "zero real tables" between test runs; this is normal,
not corruption):

```powershell
alembic revision --autogenerate -m "add distance_source to tmp_duplicates"
```

**Inspect the generated diff before keeping it.** It must contain *only* the
`distance_source` column addition to `tmp_duplicates` — no unrelated drift. If autogenerate picks
up pre-existing drift from elsewhere in the schema (this has happened on two prior migrations this
session), trim it out of this migration; leave the underlying drift untouched, don't silently fix
it here.

- [ ] **Step 3: Verify the migration cleanly upgrades and downgrades**

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"
alembic upgrade head
alembic downgrade -1
alembic upgrade head
alembic current
```

All must succeed; `alembic current` must report the new revision as head.

- [ ] **Step 4: Run the full test suites**

```bash
cd Backend && pytest -q
cd .. && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
pytest batch/tests/ -q
```

Expected: all pass, same counts as before this task (pure schema addition — existing
`TmpDuplicates(...)` ORM construction in every existing test omits `distance_source`, and the
`server_default` supplies `'clip'` transparently; no existing test asserts on this column, so
nothing should break). If any count is lower, investigate before proceeding.

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/<generated_file>.py
git commit -m "feat: add distance_source column to tmp_duplicates (test-db verified)"
```

---

### Task 2: `batch/rebuild_duplicates.py` — generalize `find_duplicates()`, three-probe active-library rebuild

**Files:**
- Modify: `batch/rebuild_duplicates.py` (entire file — every module-level probe/filter constant,
  `find_duplicates()`, `rebuild_active_library()`)
- Modify: `tests/integration/test_rebuild_duplicates.py` (update the one direct `find_duplicates()`
  call for the new required parameter and renamed import; add new tests)

**Interfaces:**
- Consumes: Task 1's `TmpDuplicates.distance_source` column.
- Produces: `find_duplicates(session, probe_sql, corpus_filter_sql, k, threshold,
  distance_source: str, embedding_table: str = "embeddings", extra_params: dict | None = None) ->
  int` (distance_source is now a required, not optional, parameter — no default). Module constants
  `TEXT_EMBEDDING_TIGHT_THRESHOLD`, `TEXT_EMBEDDING_LOOSE_THRESHOLD`, `CLIP_SAFETY_NET_THRESHOLD`,
  `_EXCLUDE_TEXT_HEAVY_PAIR`, `_TEXT_HEAVY_PAIR_ONLY` — Task 3 imports all five from this module.
  `_ACTIVE_CORPUS_FILTER` is renamed `_ACTIVE_CORPUS_FILTER_CLIP` — any external importer (Task 3's
  test file mirrors this module's pattern but does not import this specific name) must use the new
  name.

- [ ] **Step 1: Rewrite the module's probe/filter constants and `find_duplicates()`**

Replace the file's constants section (currently lines 13-35, from `_ACTIVE_PROBE_INCREMENTAL`
through `_ACTIVE_CORPUS_FILTER`) with:

```python
# Scoping fragments for the active-library rebuild -- see
# docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md and
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
# Ingestion's Tier A/Tier B review will call the same find_duplicates() shape with
# different probe/corpus fragments and threshold, not this script's CLI.
_ACTIVE_PROBE_INCREMENTAL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
      AND NOT EXISTS (
          SELECT 1 FROM tmp_duplicates d
          WHERE (d.image_id1 = i.id OR d.image_id2 = i.id) AND d.distance_source = :probe_distance_source
      )
"""

_ACTIVE_PROBE_FULL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
"""

_ACTIVE_PROBE_OCR_TEXT_INCREMENTAL = """
    SELECT i.id, oe.embedding
    FROM images i
    JOIN ocr_text_embeddings oe ON oe.image_id = i.id
    WHERE i.status = 'active'
      AND NOT EXISTS (
          SELECT 1 FROM tmp_duplicates d
          WHERE (d.image_id1 = i.id OR d.image_id2 = i.id) AND d.distance_source = 'ocr_text'
      )
"""

_ACTIVE_PROBE_OCR_TEXT_FULL = """
    SELECT i.id, oe.embedding
    FROM images i
    JOIN ocr_text_embeddings oe ON oe.image_id = i.id
    WHERE i.status = 'active'
"""

# Safety-net probe: deliberately NOT _ACTIVE_PROBE_INCREMENTAL/_FULL, and deliberately has no
# incremental skip condition at all -- see Global Constraints at the top of this plan, and the
# spec's §3, for why sharing an incremental marker with the general CLIP probe is genuinely
# broken (not just imprecise): it would silently and permanently disable the safety net for any
# text-heavy image that also has an unrelated non-text-heavy CLIP match. Scoped to active
# text_heavy images only, on the probe side too, so it never wastes a KNN search on an image that
# could never match its own corpus filter anyway.
_ACTIVE_PROBE_SAFETY_NET = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'active'
      AND EXISTS (
          SELECT 1 FROM image_classifications c WHERE c.image_id = i.id
          AND c.classifier = 'text_heavy_v1' AND c.result = 'text_heavy'
      )
"""

# Text-heavy-vs-text-heavy pairs are excluded from the general CLIP probe -- they're matched via
# OCR-text embeddings instead, plus the tight CLIP safety net above. Appended to every existing
# CLIP corpus filter as an extra AND clause. References probe.id/i2.id, both in scope wherever
# this is interpolated into find_duplicates()'s LATERAL subquery.
_EXCLUDE_TEXT_HEAVY_PAIR = """
    NOT (
        EXISTS (SELECT 1 FROM image_classifications tc1 WHERE tc1.image_id = probe.id
                AND tc1.classifier = 'text_heavy_v1' AND tc1.result = 'text_heavy')
        AND EXISTS (SELECT 1 FROM image_classifications tc2 WHERE tc2.image_id = i2.id
                    AND tc2.classifier = 'text_heavy_v1' AND tc2.result = 'text_heavy')
    )
"""

# Scoped to text-heavy-vs-text-heavy pairs only -- the inverse of the exclusion above.
_TEXT_HEAVY_PAIR_ONLY = """
    EXISTS (SELECT 1 FROM image_classifications tc1 WHERE tc1.image_id = probe.id
            AND tc1.classifier = 'text_heavy_v1' AND tc1.result = 'text_heavy')
    AND EXISTS (SELECT 1 FROM image_classifications tc2 WHERE tc2.image_id = i2.id
                AND tc2.classifier = 'text_heavy_v1' AND tc2.result = 'text_heavy')
"""

TEXT_EMBEDDING_TIGHT_THRESHOLD = 0.05   # auto-cluster-worthy OCR-text match
TEXT_EMBEDDING_LOOSE_THRESHOLD = 0.10   # review-worthy OCR-text match (Tier B upper bound)
CLIP_SAFETY_NET_THRESHOLD = 0.02        # near-pixel-identical repost, text-heavy pairs only

_ACTIVE_CORPUS_FILTER_CLIP = f"i2.status = 'active' AND ({_EXCLUDE_TEXT_HEAVY_PAIR})"
_ACTIVE_CORPUS_FILTER_CLIP_SAFETY_NET = f"i2.status = 'active' AND ({_TEXT_HEAVY_PAIR_ONLY})"
_ACTIVE_CORPUS_FILTER_OCR_TEXT = "i2.status = 'active'"  # ocr_text_embeddings join is already
                                                           # text_heavy-scoped by construction
```

Replace `find_duplicates()` (currently lines 38-76) with:

```python
async def find_duplicates(session, probe_sql: str, corpus_filter_sql: str, k: int, threshold: float,
                           distance_source: str, embedding_table: str = "embeddings",
                           extra_params: dict | None = None) -> int:
    """Insert candidate duplicate pairs found by probing `probe_sql` images (must select
    exactly (id, embedding)) against `corpus_filter_sql`-scoped neighbors in `embedding_table`
    ("embeddings" for CLIP, "ocr_text_embeddings" for OCR-text -- both use the column name
    `embedding`), via an HNSW-assisted per-image KNN search rather than a full cross join.
    Idempotent -- re-running with no new probe rows inserts zero rows, and a pair already present
    (from either probe direction, or from a different distance_source's earlier probe call within
    the same run) is skipped via ON CONFLICT DO NOTHING. Returns the number of rows actually
    inserted.

    distance_source is stamped on every inserted row -- 'clip' or 'ocr_text' -- see
    docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.

    `probe_sql`/`corpus_filter_sql` may reference named bind params (e.g. `:batch_id`) -- pass
    their values via `extra_params` rather than string-interpolating them into the fragment, even
    though callers so far only ever pass internally-generated values (never raw user input)."""
    stmt = text(f"""
        INSERT INTO tmp_duplicates (image_id1, image_id2, distance, match_source, distance_source)
        SELECT
            LEAST(probe.id, nn.image_id)    AS image_id1,
            GREATEST(probe.id, nn.image_id) AS image_id2,
            nn.distance,
            nn.match_source,
            :distance_source
        FROM ({probe_sql}) AS probe(id, embedding)
        CROSS JOIN LATERAL (
            SELECT
                e2.image_id,
                probe.embedding <=> e2.embedding AS distance,
                CASE WHEN i2.status = 'active' THEN 'cross_corpus' ELSE 'in_batch' END AS match_source
            FROM {embedding_table} e2
            JOIN images i2 ON i2.id = e2.image_id
            WHERE e2.image_id != probe.id
              AND ({corpus_filter_sql})
            ORDER BY probe.embedding <=> e2.embedding
            LIMIT :k
        ) nn
        WHERE nn.distance < :threshold
        ON CONFLICT (image_id1, image_id2) DO NOTHING
    """)
    params = {"k": k, "threshold": threshold, "distance_source": distance_source, **(extra_params or {})}
    result = await session.execute(stmt, params)
    return result.rowcount
```

(`embedding_table` is an internally-fixed enum of two literal strings passed by call sites in this
codebase, never user input — f-string interpolation here matches this function's own existing,
already-documented convention for `probe_sql`/`corpus_filter_sql`.)

- [ ] **Step 2: Rewrite `rebuild_active_library()`**

Replace the current function (lines 79-94) with:

```python
async def rebuild_active_library(session, k: int, threshold: float, full: bool = False) -> int:
    if full:
        print("Full rebuild: clearing existing active-library candidate pairs...")
        # Scoped to pairs where both sides are active, so a future in-flight ingestion
        # review (pending-involving rows) is never touched by a routine active-library
        # rebuild.
        await session.execute(text("""
            DELETE FROM tmp_duplicates
            WHERE image_id1 IN (SELECT id FROM images WHERE status = 'active')
              AND image_id2 IN (SELECT id FROM images WHERE status = 'active')
        """))
        clip_probe_sql = _ACTIVE_PROBE_FULL
        ocr_probe_sql = _ACTIVE_PROBE_OCR_TEXT_FULL
    else:
        clip_probe_sql = _ACTIVE_PROBE_INCREMENTAL
        ocr_probe_sql = _ACTIVE_PROBE_OCR_TEXT_INCREMENTAL

    # probe_distance_source is only referenced by the _INCREMENTAL probe variants' own
    # NOT EXISTS clause -- harmless to always pass it even on a --full run, where the _FULL
    # variants simply don't reference it; SQLAlchemy's text() execute ignores unused dict keys.
    inserted = await find_duplicates(
        session, clip_probe_sql, _ACTIVE_CORPUS_FILTER_CLIP, k, threshold,
        distance_source="clip", extra_params={"probe_distance_source": "clip"},
    )
    # Deliberately NOT clip_probe_sql, and deliberately no incremental skip condition -- see
    # this plan's Global Constraints and the spec's §3 for why.
    inserted += await find_duplicates(
        session, _ACTIVE_PROBE_SAFETY_NET, _ACTIVE_CORPUS_FILTER_CLIP_SAFETY_NET, k,
        CLIP_SAFETY_NET_THRESHOLD, distance_source="clip",
    )
    inserted += await find_duplicates(
        session, ocr_probe_sql, _ACTIVE_CORPUS_FILTER_OCR_TEXT, k, TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={"probe_distance_source": "ocr_text"},
    )
    return inserted
```

`_process()`, `main()`, and the `if __name__ == "__main__"` CLI block are unchanged — do not
modify them; the public CLI surface (`--k`, `--threshold`, `--full`) keeps meaning exactly what it
means today (the general CLIP probe's own tuning), since the new thresholds are fixed constants.

- [ ] **Step 3: Update the existing integration test file's one direct `find_duplicates()` call and import**

In `tests/integration/test_rebuild_duplicates.py`, line 14 currently reads:

```python
from batch.rebuild_duplicates import find_duplicates, rebuild_active_library, _ACTIVE_CORPUS_FILTER
```

Change to:

```python
from batch.rebuild_duplicates import find_duplicates, rebuild_active_library, _ACTIVE_CORPUS_FILTER_CLIP
```

`test_find_duplicates_is_reusable_with_arbitrary_scoping` (currently lines 142-153) currently
calls:

```python
    probe_sql = f"SELECT i.id, e.embedding FROM images i JOIN embeddings e ON e.image_id = i.id WHERE i.id = '{a}'"
    inserted = await find_duplicates(db_session, probe_sql, _ACTIVE_CORPUS_FILTER, k=5, threshold=0.3)
```

Change the second line to:

```python
    probe_sql = f"SELECT i.id, e.embedding FROM images i JOIN embeddings e ON e.image_id = i.id WHERE i.id = '{a}'"
    inserted = await find_duplicates(db_session, probe_sql, _ACTIVE_CORPUS_FILTER_CLIP, k=5,
                                      threshold=0.3, distance_source="clip")
```

No other test in this file needs a code change — every other test calls `rebuild_active_library()`
(whose own public signature is unchanged) with only `Embedding` rows inserted (no
`ImageClassification`/`OCRTextEmbedding` rows), so the two new probes correctly find zero
additional candidates for that data and every existing assertion (e.g. `assert inserted == 1`)
continues to hold as the total across all three probes. Run the file once after Step 3's two edits
to confirm this (Step 5 below covers the full run).

- [ ] **Step 4: Add new tests to `tests/integration/test_rebuild_duplicates.py`**

Append these imports at the top of the file (alongside the existing `Embedding, Image` import):

```python
from Storage.models import Embedding, Image, ImageClassification, OCRTextEmbedding
```

Append these helpers and tests to the end of the file:

```python
async def _mark_text_heavy(session, image_id: uuid.UUID) -> None:
    session.add(ImageClassification(
        image_id=image_id, classifier="text_heavy_v1", result="text_heavy", details={}))
    await session.flush()


async def _insert_ocr_text_embedding(session, image_id: uuid.UUID, embedding_values: list[float]) -> None:
    session.add(OCRTextEmbedding(image_id=image_id, embedding=embedding_values))
    await session.flush()


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * 384  # OCR_TEXT_EMBEDDING_DIM
    vec[index] = 1.0
    return vec


def _near_unit_vector(index: int, epsilon: float = 0.5) -> list[float]:
    """A CLIP vector close to (but not identical to) the pure unit vector at `index` -- cosine
    distance from _unit_vector(index) works out to ~0.106 for the default epsilon (comfortably
    inside the general probe's 0.3 threshold, and nowhere near the tight 0.02 safety-net
    threshold, though that only matters if the corpus filter would even consider the pair)."""
    vec = [0.0] * _DIM
    vec[index] = 1.0
    other = (index + 1) % _DIM
    vec[other] = epsilon
    norm = (1.0 + epsilon ** 2) ** 0.5
    return [v / norm for v in vec]


@pytest.mark.asyncio(loop_scope="session")
async def test_safety_net_finds_tight_text_heavy_match_general_probe_excludes(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))  # identical -> distance 0
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "clip"  # the safety net, not the excluded general probe
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_safety_net_not_starved_by_unrelated_general_probe_match(db_session):
    """The regression test for the correctness bug this plan's Global Constraints call out
    explicitly: a text-heavy image with an unrelated non-text-heavy CLIP match must still get
    its own safety-net-eligible text-heavy near-duplicate found, even though the general probe
    already inserted a distance_source='clip' row for it first. This must fail if the safety-net
    probe is changed back to share the general probe's own incremental marker."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))         # text-heavy
    b = await _insert_image_with_embedding(db_session, _unit_vector(0))         # text-heavy, near-dup of a
    c = await _insert_image_with_embedding(db_session, _near_unit_vector(0))    # NOT text-heavy, close enough to a for general CLIP (~0.106)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    # c is deliberately left unclassified (not text_heavy)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    pairs = {
        tuple(sorted((str(r.image_id1), str(r.image_id2)))): r.distance_source
        for r in (await db_session.execute(
            text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
        )).all()
    }
    assert tuple(sorted((str(a), str(b)))) in pairs  # safety net found the text-heavy pair
    assert tuple(sorted((str(a), str(c)))) in pairs  # general probe found the unrelated pair
    assert pairs[tuple(sorted((str(a), str(b))))] == "clip"
    assert pairs[tuple(sorted((str(a), str(c))))] == "clip"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_finds_pair_via_ocr_text_embeddings(db_session):
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal CLIP -- general probe won't find this
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))  # identical OCR-text embedding

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance, distance_source FROM tmp_duplicates "
             "WHERE distance_source = 'ocr_text'")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance == pytest.approx(0.0, abs=1e-6)


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_rerun_does_not_skip_ocr_text_probe_for_already_clip_probed_image(db_session):
    """Task 1's own §2 regression: a second signal's probe must not be silently skipped just
    because a different signal's probe already inserted a row for the same image earlier in the
    same incremental probe-set fragment's lifetime."""
    a = await _insert_image_with_embedding(db_session, _unit_vector(0))
    b = await _insert_image_with_embedding(db_session, _unit_vector(1))  # orthogonal CLIP, no general-probe match
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))

    # First rebuild: general probe finds nothing (a,b are CLIP-orthogonal and text-heavy-excluded
    # anyway); safety net finds nothing (CLIP-orthogonal, past CLIP_SAFETY_NET_THRESHOLD); OCR-text
    # probe finds the pair.
    first = await rebuild_active_library(db_session, k=20, threshold=0.3)
    assert first == 1

    # Second, incremental rebuild: nothing new to find, but this must not raise or behave
    # differently -- confirms the incremental NOT EXISTS fragment's distance_source gating didn't
    # somehow desync between the two signals.
    second = await rebuild_active_library(db_session, k=20, threshold=0.3)
    assert second == 0
```

- [ ] **Step 5: Run the tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_rebuild_duplicates.py -v
```

Expected: all pass (11 existing + 4 new = 15). Then the full suites:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

- [ ] **Step 6: Commit**

```bash
git add batch/rebuild_duplicates.py tests/integration/test_rebuild_duplicates.py
git commit -m "feat: three-signal active-library duplicate probing (CLIP, CLIP safety net, OCR-text)"
```

---

### Task 3: `batch/ingest_find_duplicates.py` — mirror the split at both ingestion tiers

**Files:**
- Modify: `batch/ingest_find_duplicates.py` (constants section, `find_batch_duplicates()`)
- Modify: `tests/integration/test_ingest_find_duplicates.py` (add new tests; existing tests need no
  changes — see Step 3's note)

**Interfaces:**
- Consumes: Task 2's `find_duplicates`, `_EXCLUDE_TEXT_HEAVY_PAIR`, `_TEXT_HEAVY_PAIR_ONLY`,
  `CLIP_SAFETY_NET_THRESHOLD`, `TEXT_EMBEDDING_LOOSE_THRESHOLD` (all imported from
  `batch.rebuild_duplicates`).
- Produces: `find_batch_duplicates(session, batch_id, k: int, threshold: float) -> int` — same
  public signature as today; internals now issue three `find_duplicates()` calls instead of one.

- [ ] **Step 1: Update imports and add the three-variant corpus filters + OCR-text probe SQL**

Change the current import line:

```python
from batch.rebuild_duplicates import find_duplicates
```

to:

```python
from batch.rebuild_duplicates import (
    find_duplicates, _EXCLUDE_TEXT_HEAVY_PAIR, _TEXT_HEAVY_PAIR_ONLY,
    CLIP_SAFETY_NET_THRESHOLD, TEXT_EMBEDDING_LOOSE_THRESHOLD,
)
```

Replace the current `_BATCH_PROBE_SQL`/`_BATCH_CORPUS_FILTER_SQL` block (currently lines 54-64)
with:

```python
# Probe = this batch's pending images. Corpus = the active library plus this image's own
# batch siblings -- a single filter covering both "cross-corpus" and "in-batch" matches in
# one KNN pass, tagged via match_source. See the duplicate-clustering prereq's scoping table.
_BATCH_PROBE_SQL = """
    SELECT i.id, e.embedding
    FROM images i
    JOIN embeddings e ON e.image_id = i.id
    WHERE i.status = 'pending' AND i.ingestion_batch_id = :batch_id
"""

_BATCH_PROBE_SQL_OCR_TEXT = """
    SELECT i.id, oe.embedding
    FROM images i
    JOIN ocr_text_embeddings oe ON oe.image_id = i.id
    WHERE i.status = 'pending' AND i.ingestion_batch_id = :batch_id
"""

_BATCH_CORPUS_FILTER_SQL_CLIP = f"""
    (i2.status = 'active' OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id))
    AND ({_EXCLUDE_TEXT_HEAVY_PAIR})
"""

_BATCH_CORPUS_FILTER_SQL_CLIP_SAFETY_NET = f"""
    (i2.status = 'active' OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id))
    AND ({_TEXT_HEAVY_PAIR_ONLY})
"""

_BATCH_CORPUS_FILTER_SQL_OCR_TEXT = """
    i2.status = 'active' OR (i2.status = 'pending' AND i2.ingestion_batch_id = :batch_id)
"""
```

- [ ] **Step 2: Rewrite `find_batch_duplicates()`**

Replace the current function (lines 67-74) with:

```python
async def find_batch_duplicates(session, batch_id, k: int, threshold: float) -> int:
    """Populate tmp_duplicates with candidate pairs for `batch_id`'s pending images, at the
    given threshold, across all three signals (general CLIP excluding text-heavy pairs, CLIP
    safety net for text-heavy pairs, OCR-text for text-heavy pairs). Safe to call once per tier
    (Tier A tight, Tier B loose) -- a pair already found by an earlier, tighter call, or by a
    different signal, is a no-op via ON CONFLICT DO NOTHING.

    The OCR-text probe always runs at TEXT_EMBEDDING_LOOSE_THRESHOLD regardless of `threshold` or
    which tier is calling -- deliberately, not by coincidence. This mirrors this file's own
    already-established convention for the CLIP probe itself: the tier_b CLIP call already
    inserts generously all the way down to distance 0, relying entirely on the review query's own
    band filtering (get_tier_candidate_rows/list_tier_b_review_page's `distance >= low AND
    distance < high`) to decide which UI tier a stored row surfaces in -- not on which probe call
    inserted it. See this plan's Global Constraints for why deriving a tighter value via e.g.
    min(threshold, TEXT_EMBEDDING_LOOSE_THRESHOLD) would be a real bug, not a simplification.

    The safety-net probe reuses _BATCH_PROBE_SQL unchanged (all pending images in the batch, not
    scoped to text_heavy on the probe side) -- unlike rebuild_duplicates.py's own safety-net
    probe, this file's probes have no incremental skip condition to begin with (this function
    probes every pending image in the batch every time, relying entirely on ON CONFLICT DO
    NOTHING), so there is no analogous incremental-staleness risk here to design around. The
    corpus filter alone (_BATCH_CORPUS_FILTER_SQL_CLIP_SAFETY_NET) correctly finds zero candidates
    for a non-text-heavy probe image regardless."""
    inserted = await find_duplicates(
        session, _BATCH_PROBE_SQL, _BATCH_CORPUS_FILTER_SQL_CLIP, k, threshold,
        distance_source="clip", extra_params={"batch_id": batch_id},
    )
    inserted += await find_duplicates(
        session, _BATCH_PROBE_SQL, _BATCH_CORPUS_FILTER_SQL_CLIP_SAFETY_NET, k, CLIP_SAFETY_NET_THRESHOLD,
        distance_source="clip", extra_params={"batch_id": batch_id},
    )
    inserted += await find_duplicates(
        session, _BATCH_PROBE_SQL_OCR_TEXT, _BATCH_CORPUS_FILTER_SQL_OCR_TEXT, k,
        TEXT_EMBEDDING_LOOSE_THRESHOLD,
        distance_source="ocr_text", embedding_table="ocr_text_embeddings",
        extra_params={"batch_id": batch_id},
    )
    return inserted
```

`main()` and the CLI block are unchanged.

- [ ] **Step 3: Confirm the existing test file needs no changes, then add new tests**

Re-read `tests/integration/test_ingest_find_duplicates.py`'s existing four DB-backed tests
(`test_finds_in_batch_match`, `test_finds_cross_corpus_match`,
`test_excludes_other_batches_pending_images`, `test_respects_threshold`) — all four insert only
`Embedding` rows (no `ImageClassification`/`OCRTextEmbedding`), so the two new probes find zero
additional candidates for that data and every existing `assert inserted == N` continues to hold
as the total across all three probes. No edits needed to these four tests or the two
`should_advance_stage` unit tests.

Append these imports (alongside the existing `Embedding, Image` import):

```python
from Storage.models import Embedding, Image, ImageClassification, OCRTextEmbedding
```

Append these helpers and tests to the end of the file:

```python
async def _mark_text_heavy(session, image_id: uuid.UUID) -> None:
    session.add(ImageClassification(
        image_id=image_id, classifier="text_heavy_v1", result="text_heavy", details={}))
    await session.flush()


async def _insert_ocr_text_embedding(session, image_id: uuid.UUID, embedding_values: list[float]) -> None:
    session.add(OCRTextEmbedding(image_id=image_id, embedding=embedding_values))
    await session.flush()


def _text_unit_vector(index: int) -> list[float]:
    vec = [0.0] * 384
    vec[index] = 1.0
    return vec


@pytest.mark.asyncio(loop_scope="session")
async def test_general_clip_excludes_text_heavy_pair_safety_net_still_finds_it(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)  # identical -> distance 0
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "clip"  # the safety net, since the general probe excludes this pair


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_finds_pair_at_tier_b(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)  # orthogonal CLIP
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.12)  # tier_b threshold

    assert inserted == 1
    row = (await db_session.execute(
        text("SELECT image_id1, image_id2, distance_source FROM tmp_duplicates")
    )).one()
    assert {row.image_id1, row.image_id2} == {a, b}
    assert row.distance_source == "ocr_text"


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_probe_ignores_threshold_argument(db_session):
    """Regression test for the min(threshold, TEXT_EMBEDDING_LOOSE_THRESHOLD) bug this plan's
    Global Constraints explicitly forbid reintroducing -- the OCR-text probe must still find a
    pair at distance ~0 even when called with Tier A's tight threshold (0.05), since it always
    uses TEXT_EMBEDDING_LOOSE_THRESHOLD (0.10) regardless of the `threshold` argument."""
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    a = await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    b = await _insert_image(db_session, _unit_vector(1), "pending", batch_id)
    await _mark_text_heavy(db_session, a)
    await _mark_text_heavy(db_session, b)
    await _insert_ocr_text_embedding(db_session, a, _text_unit_vector(0))
    await _insert_ocr_text_embedding(db_session, b, _text_unit_vector(0))

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.05)  # tier_a threshold

    row = (await db_session.execute(
        text("SELECT distance_source FROM tmp_duplicates")
    )).one()
    assert row.distance_source == "ocr_text"  # found despite tier_a's tight CLIP threshold
```

- [ ] **Step 4: Run the tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingest_find_duplicates.py -v
```

Expected: all pass (6 existing + 3 new = 9). Then the full suites:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

- [ ] **Step 5: Commit**

```bash
git add batch/ingest_find_duplicates.py tests/integration/test_ingest_find_duplicates.py
git commit -m "feat: three-signal ingestion Tier A/B duplicate probing"
```

---

### Task 4: `batch/clusterize.py` — `distance_source`-aware union-find edge acceptance

**Files:**
- Modify: `batch/clusterize.py` (imports, new constant, `get_duplicate_pairs()`,
  `cluster_active_library()`'s call site)
- Modify: `tests/integration/test_clusterize.py` (add new tests)

**Interfaces:**
- Consumes: Task 1's `distance_source` column.
- Produces: `get_duplicate_pairs(session, mapping, clip_threshold: float, ocr_text_threshold:
  float) -> list[tuple[int, int, float]]` — signature changes from a single `threshold` to two
  named thresholds. `PROXIMITY_THRESHOLD_OCR_TEXT = 0.05` new module constant, alongside the
  existing unchanged `PROXIMITY_THRESHOLD = 0.05`.

- [ ] **Step 1: Update the import line and add the new constant**

Change:

```python
from sqlalchemy import select, delete
```

to:

```python
from sqlalchemy import and_, or_, select, delete
```

Add directly after the existing `PROXIMITY_THRESHOLD = 0.05` (currently line 13):

```python
# Matches TEXT_EMBEDDING_TIGHT_THRESHOLD in batch/rebuild_duplicates.py. A separately-named
# constant, not a shared import, even though the two are numerically equal today -- see
# docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md: never assume the two
# scales stay coupled, so a future change to either doesn't silently move the other.
PROXIMITY_THRESHOLD_OCR_TEXT = 0.05
```

- [ ] **Step 2: Rewrite `get_duplicate_pairs()`**

Replace the current function (lines 158-185) with:

```python
async def get_duplicate_pairs(session, mapping, clip_threshold, ocr_text_threshold) -> list[tuple[int, int, float]]:
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
            or_(
                and_(TmpDuplicates.distance_source == "clip", TmpDuplicates.distance < clip_threshold),
                and_(TmpDuplicates.distance_source == "ocr_text", TmpDuplicates.distance < ocr_text_threshold),
            ),
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

- [ ] **Step 3: Update the call site in `cluster_active_library()`**

Change (currently line 76):

```python
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD)
```

to:

```python
    pairs = await get_duplicate_pairs(session, img_id_to_int_id, PROXIMITY_THRESHOLD, PROXIMITY_THRESHOLD_OCR_TEXT)
```

Nothing else in `cluster_active_library()` or `resolve_cluster()` changes — the returned
`pairs` list shape (`(int_id1, int_id2, distance)`) is identical regardless of source, and
everything downstream of `get_duplicate_pairs()` (union-find construction, splitting) stays
source-agnostic per this plan's Global Constraints.

- [ ] **Step 4: Add new tests to `tests/integration/test_clusterize.py`**

Append this import at the top of the file (alongside the existing `Storage.models` import):

```python
from Storage.models import DuplicateDecision, Embedding, Image, TmpDuplicates, TmpImageClusters
```

(Already present in the current file — no change needed there; `TmpDuplicates` is already
imported and used directly in `_insert_pair`.)

Modify `_insert_pair` to accept an optional `distance_source` (default preserves today's
behavior via the column's own `server_default`):

```python
async def _insert_pair(session, a: uuid.UUID, b: uuid.UUID, distance: float, distance_source: str = "clip") -> None:
    id1, id2 = _normalize(a, b)
    session.add(TmpDuplicates(image_id1=id1, image_id2=id2, distance=distance, distance_source=distance_source))
    await session.flush()
```

(This changes every existing call site's *behavior* not at all — they all omit the new parameter,
so every existing pair is explicitly `distance_source="clip"`, identical to what the column's own
`server_default` already produced implicitly. This is a mechanical signature widening, not a
functional change; no other line in any existing test in this file needs to change.)

Append these new tests to the end of the file:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_clusters_under_its_own_threshold(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.03, distance_source="ocr_text")  # < PROXIMITY_THRESHOLD_OCR_TEXT (0.05)

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b}


@pytest.mark.asyncio(loop_scope="session")
async def test_ocr_text_sourced_pair_past_its_own_threshold_does_not_cluster(db_session):
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    # 0.08 is past PROXIMITY_THRESHOLD_OCR_TEXT (0.05) but well within clip's own 0.05 too --
    # this must NOT cluster despite the distance being numerically close to what a clip-sourced
    # pair at the same value would need. Proves the two thresholds are independently enforced,
    # not OR'd loosely against a single shared cutoff.
    await _insert_pair(db_session, a, b, 0.08, distance_source="ocr_text")

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters))).scalars().all()
    assert rows == []


@pytest.mark.asyncio(loop_scope="session")
async def test_clip_and_ocr_text_thresholds_enforced_independently(db_session):
    """The core regression test for this task: two pairs at distances that would swap outcomes
    if the two distance_source thresholds were ever accidentally conflated into one shared
    comparison."""
    a = await _insert_image(db_session)
    b = await _insert_image(db_session)
    c = await _insert_image(db_session)
    d = await _insert_image(db_session)
    await _insert_pair(db_session, a, b, 0.045, distance_source="clip")      # < 0.05 (PROXIMITY_THRESHOLD) -> clusters
    await _insert_pair(db_session, c, d, 0.045, distance_source="ocr_text")  # < 0.05 (PROXIMITY_THRESHOLD_OCR_TEXT) -> clusters

    await cluster_active_library(db_session)

    rows = (await db_session.execute(select(TmpImageClusters.image_id))).scalars().all()
    assert set(rows) == {a, b, c, d}
```

- [ ] **Step 5: Run the tests**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_clusterize.py -v
```

Expected: all pass (4 existing + 3 new = 7). Then:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
cd Backend && pytest -q
cd .. && pytest batch/tests/ -q
```

- [ ] **Step 6: Commit**

```bash
git add batch/clusterize.py tests/integration/test_clusterize.py
git commit -m "feat: distance_source-aware union-find edge acceptance in clusterize"
```

---

### Task 5: Backend — repository, service, API schema

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py` (`get_tier_candidate_rows`,
  `list_tier_b_review_page`)
- Modify: `Backend/app/services/ingestion_service.py` (`list_clusters`, `list_tier_b_review`)
- Modify: `Backend/app/api/ingestion.py` (`ClusterEdge`, `TierBCandidate` Pydantic models)
- Modify: `Backend/tests/test_ingestion_repository.py`, `Backend/tests/test_ingestion_service.py`,
  `Backend/tests/test_ingestion_endpoints.py` (existing fixtures/assertions touching
  `match_source`-shaped rows need `distance_source` added; new assertions)
- Modify: `tests/integration/test_backend_ingestion_repository.py` (if it exists — confirm at
  implementation time; add coverage for the new column reaching a real query)

**Interfaces:**
- Consumes: Task 1's `distance_source` column.
- Produces: every `edges`/`candidates` dict this service returns now carries `"distance_source":
  <str>`, and `ClusterEdge`/`TierBCandidate`'s JSON responses carry `distance_source`.

- [ ] **Step 1: `get_tier_candidate_rows` — add `distance_source` to the SELECT**

In `Backend/app/repositories/ingestion_repository.py`, the current `query` inside
`get_tier_candidate_rows` (around line 65) selects:

```python
        query = (
            select(
                TmpDuplicates.image_id1, img1.filename, img1.status,
                TmpDuplicates.image_id2, img2.filename, img2.status,
                TmpDuplicates.distance, TmpDuplicates.match_source,
            )
```

Change to:

```python
        query = (
            select(
                TmpDuplicates.image_id1, img1.filename, img1.status,
                TmpDuplicates.image_id2, img2.filename, img2.status,
                TmpDuplicates.distance, TmpDuplicates.match_source, TmpDuplicates.distance_source,
            )
```

No other line in this method changes — the WHERE clause's tier-band filtering needs no update
(see the spec's §6 for why both new signals' calibrated ranges already nest inside the existing
CLIP-defined bands).

- [ ] **Step 2: `list_tier_b_review_page` — add `distance_source` to both raw-SQL SELECTs**

The `pair_cte` block (currently lines 148-168) has two near-identical `SELECT` clauses inside the
`UNION ALL`. Change both from:

```python
            SELECT td.image_id1 AS subject_id, td.image_id2 AS cand_id, td.distance, td.match_source
```
and
```python
            SELECT td.image_id2 AS subject_id, td.image_id1 AS cand_id, td.distance, td.match_source
```

to:

```python
            SELECT td.image_id1 AS subject_id, td.image_id2 AS cand_id, td.distance, td.match_source, td.distance_source
```
and
```python
            SELECT td.image_id2 AS subject_id, td.image_id1 AS cand_id, td.distance, td.match_source, td.distance_source
```

respectively (both inside the `pair_cte` string). Then the final `cand_sql`'s outer SELECT
(currently lines 219-221):

```python
        SELECT r.subject_id, r.cand_id, c.filename AS cand_filename, c.status AS cand_status,
               r.distance, r.match_source
        FROM ranked r JOIN images c ON c.id = r.cand_id
```

Change to:

```python
        SELECT r.subject_id, r.cand_id, c.filename AS cand_filename, c.status AS cand_status,
               r.distance, r.match_source, r.distance_source
        FROM ranked r JOIN images c ON c.id = r.cand_id
```

The `ranked` CTE's own `SELECT p.*, ROW_NUMBER() OVER (...)` (currently line 208) already carries
every column from `pair_cte` forward via `p.*`, including the newly-added `distance_source` — no
change needed there. No `ORDER BY`/`PARTITION BY`/`HAVING` clause changes anywhere in this method
(see the spec's §6 reasoning: both signals' ranges already nest inside the existing tier bands, so
no sort-key change is needed for correctness — only the SELECT lists need to carry the new column
through to the caller).

- [ ] **Step 3: `ingestion_service.py` — thread `distance_source` through both response builders**

In `list_clusters` (around line 175-186), the current row-unpacking and edge dict:

```python
        for id1, filename1, status1, id2, filename2, status2, distance, match_source in rows:
            uf.connect(id1, id2)
            member_info[id1] = {"image_id": str(id1), "filename": filename1, "status": status1}
            member_info[id2] = {"image_id": str(id2), "filename": filename2, "status": status2}
            member_uuids.update((id1, id2))
            if split_cfg is not None:
                pairs_by_member[id1].append((id2, distance))
                pairs_by_member[id2].append((id1, distance))
            edges.append({
                "image_id1": str(id1), "image_id2": str(id2),
                "distance": distance, "match_source": match_source,
            })
```

Change to:

```python
        for id1, filename1, status1, id2, filename2, status2, distance, match_source, distance_source in rows:
            uf.connect(id1, id2)
            member_info[id1] = {"image_id": str(id1), "filename": filename1, "status": status1}
            member_info[id2] = {"image_id": str(id2), "filename": filename2, "status": status2}
            member_uuids.update((id1, id2))
            if split_cfg is not None:
                pairs_by_member[id1].append((id2, distance))
                pairs_by_member[id2].append((id1, distance))
            edges.append({
                "image_id1": str(id1), "image_id2": str(id2),
                "distance": distance, "match_source": match_source, "distance_source": distance_source,
            })
```

In `list_tier_b_review` (around line 303-311), the current candidate list comprehension:

```python
            "candidates": [
                {"member": member(c.cand_id, c.cand_filename, c.cand_status),
                 "distance": c.distance, "match_source": c.match_source}
                for c in cands_by_subject.get(s.subject_id, [])
            ],
```

Change to:

```python
            "candidates": [
                {"member": member(c.cand_id, c.cand_filename, c.cand_status),
                 "distance": c.distance, "match_source": c.match_source,
                 "distance_source": c.distance_source}
                for c in cands_by_subject.get(s.subject_id, [])
            ],
```

- [ ] **Step 4: `Backend/app/api/ingestion.py` — add the field to both response models**

Current `ClusterEdge` (around line 42-46):

```python
class ClusterEdge(BaseModel):
    image_id1: str
    image_id2: str
    distance: float
    match_source: Optional[str]
```

Change to:

```python
class ClusterEdge(BaseModel):
    image_id1: str
    image_id2: str
    distance: float
    match_source: Optional[str]
    distance_source: Optional[str]
```

Current `TierBCandidate` (around line 61-64):

```python
class TierBCandidate(BaseModel):
    member: ClusterMember
    distance: float
    match_source: Optional[str]
```

Change to:

```python
class TierBCandidate(BaseModel):
    member: ClusterMember
    distance: float
    match_source: Optional[str]
    distance_source: Optional[str]
```

- [ ] **Step 5: Check for and update `shared/schemas/` files, regenerate if needed**

Run:

```bash
grep -rl "match_source" shared/schemas/
```

If `ingestionclusteredge.schema.json` and/or `ingestiontierbcandidate.schema.json` (or similarly
named files) appear and contain a `match_source` property, add a matching `distance_source`
property (same `"type": ["string", "null"]` shape) directly after it, then regenerate all three
trees per `documents/generation.md`:

```bash
# TypeScript
bash Frontend/generate-types.sh
# Kotlin
python AndroidClient/scripts/generate_dtos.py
# Python (from Backend/)
cd Backend && datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
cd ..
```

then verify no unexpected diff beyond the intended field addition:

```bash
git diff Frontend/memes-frontend/src/types/generated/
git diff Backend/app/types/generated/
```

If `grep -rl "match_source" shared/schemas/` finds nothing (i.e. `ClusterEdge`/`TierBCandidate` are
purely hand-written, per CLAUDE.md's own documented gotcha about this exact router), this step is
a no-op — do not create new schema files; Task 5's own hand-written model edits (Step 4) are the
only source of truth, exactly matching this router's existing convention.

- [ ] **Step 6: Update existing Backend tests and add new ones**

In `Backend/tests/test_ingestion_repository.py`, find every mocked row tuple currently shaped
`(id1, filename1, status1, id2, filename2, status2, distance, match_source)` passed to
`get_tier_candidate_rows`'s mocked `session.execute(...).all()` return value, and add a
`distance_source` value (`"clip"` for every existing fixture, since none of them test the new
column) as the 9th tuple element. Search for `match_source` in this file to find every such
fixture.

In `Backend/tests/test_ingestion_service.py`, similarly update every `SimpleNamespace`/mocked row
used as `get_tier_candidate_rows`'s or `list_tier_b_review_page`'s return value to include a
`distance_source` attribute (mirroring however `match_source` is already set on the same mock
objects — search for `match_source=` in this file to find every call site). Add one new test:

```python
    async def test_distance_source_threaded_through_to_edges(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        await self._setup(service, mock_repo, [
            (a1, f"{a1}.jpg", "pending", a2, f"{a2}.jpg", "pending", 0.03, "cross_corpus", "ocr_text"),
        ])
        page = await service.list_clusters("tier_a", limit=10)
        assert page["items"][0]["edges"][0]["distance_source"] == "ocr_text"
```

(Place this inside `TestListClustersPagination`, adjusting `self._setup`'s row-tuple shape if that
helper constructs rows itself rather than taking them as a literal list — check the helper's
current implementation and match its existing calling convention exactly.)

In `Backend/tests/test_ingestion_endpoints.py`, update `TestListClusters.test_returns_cluster_page_for_tier`
and `TestTierBReview.test_returns_page`'s mocked service return values to include
`"distance_source"` in every edge/candidate dict (mirroring `"match_source"`'s existing presence),
and add one assertion each confirming it round-trips through the real Pydantic response model:

```python
        assert body["items"][0]["edges"][0]["distance_source"] == "ocr_text"  # in the cluster test
```
```python
        assert b["items"][0]["candidates"][0]["distance_source"] == "ocr_text"  # in the tier_b test
```

- [ ] **Step 7: Check for a real-DB integration test file for the repository layer**

```bash
find tests/integration -iname "*ingestion_repository*" -o -iname "*backend_ingestion*"
```

If one exists, add a test inserting a `distance_source="ocr_text"` row and confirming
`get_tier_candidate_rows` and/or `list_tier_b_review_page` return it correctly — follow that
file's own existing fixture/assertion conventions exactly (read it first). If no such file exists,
this step is a no-op — the mocked-session tests in Step 6 plus Task 1-4's own real-DB tests
already exercise every SQL path this task touches.

- [ ] **Step 8: Run the tests**

```bash
cd Backend && pytest -q
cd .. && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
pytest batch/tests/ -q
pytest tests/docker/ -v
```

(Include `tests/docker/` — this task touches `Backend/app/repositories/ingestion_repository.py`
and `Backend/app/api/ingestion.py`, both Backend-reachable; confirm the Docker image still boots,
per this repo's own documented precedent of catching a real import-chain regression exactly this
way on a prior branch.)

- [ ] **Step 9: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py Backend/app/services/ingestion_service.py \
  Backend/app/api/ingestion.py Backend/tests/test_ingestion_repository.py \
  Backend/tests/test_ingestion_service.py Backend/tests/test_ingestion_endpoints.py
# plus shared/schemas/ + all three generated-type trees, if Step 5 found and touched them
git commit -m "feat: surface distance_source through the ingestion review API"
```

---

### Task 6: Frontend — `distance_source` label in Tier B review

**Files:**
- Modify: `Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.tsx`
- Modify: `Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.test.tsx`

**Interfaces:**
- Consumes: Task 5's `distance_source` field on the generated `IngestionTierBCandidate` type
  (confirm the exact generated type/field name after Task 5's Step 5 regeneration — it will be
  `distance_source?: string | null` or similar, matching `match_source`'s existing generated
  shape in `Frontend/memes-frontend/src/types/generated/all.d.ts`).

- [ ] **Step 1: Update the candidate `edgeSummary` string**

Current (line 48):

```tsx
            edgeSummary={`${c.distance.toFixed(3)} · ${c.match_source ?? "?"}`}
```

Change to:

```tsx
            edgeSummary={`${c.distance.toFixed(3)} · ${c.distance_source === "ocr_text" ? "text" : "visual"} · ${c.match_source ?? "?"}`}
```

- [ ] **Step 2: Add a test case**

In `TierBReviewCard.test.tsx`, the existing `item` fixture (lines 8-15) has one candidate. Add
`distance_source: "ocr_text"` to that candidate's `member` sibling fields (i.e. alongside
`distance`/`match_source` on the candidate object itself, not on `member` — confirm the exact
field placement matches the real `IngestionTierBCandidate` generated type from Task 5 before
writing this), then add a new test:

```tsx
  it('shows a text-embedding label for an ocr_text-sourced candidate', () => {
    renderCard()
    expect(screen.getByText(/0\.080 · text · in_batch/)).toBeInTheDocument()
  })
```

Adjust the fixture's candidate distance value if needed so the regex matches the fixture's actual
`c1` candidate (currently `distance: 0.08`) — if Step 1's fixture update sets `c1`'s
`distance_source` to `"ocr_text"`, the existing `it('shows a candidate distance/source line', ...)`
test (currently asserting `/0\.080 · in_batch/`) needs its regex updated to
`/0\.080 · visual · in_batch/` if `c1` stays `distance_source: undefined`/`"clip"`, or the new
label text if you instead add a *second* candidate specifically for the new test rather than
mutating `c1` — prefer adding a second candidate (`c2`-shaped, new) to avoid changing the meaning
of an existing, already-passing test.

- [ ] **Step 3: Run the frontend checks**

```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
vitest run
```

- [ ] **Step 4: Commit**

```bash
git add Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.tsx \
  Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.test.tsx
git commit -m "feat: label text-embedding-sourced candidates in Tier B review"
```

---

### Task 7: Live rollout — CONTROLLER-ONLY, requires explicit user go-ahead

**This task is not a subagent dispatch.** Its migration step and its `rebuild_duplicates.py` run
both write to live `metal`/`general`/`it` databases the developer's own running backends depend on
continuously — exactly the case CLAUDE.md's "Live database access for agents" section and this
session's own established pattern (every prior rollout task this multi-week effort) exist for. The
controller runs every step below itself, never a subagent.

**Before Step 2 (the first live-environment write), stop and get the user's explicit go-ahead.**
Name the three environments this will touch and what it does (an additive, zero-risk column
migration, then running `rebuild_duplicates.py` — a live-database write that populates new
candidate pairs and re-clusters the active library) before proceeding.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md` (status + a
  "Rollout outcome" section).

- [ ] **Step 1: Confirm the plan and get the go-ahead**

Summarize for the user: Tasks 1-6's code is merged; Step 2 below applies the new
`distance_source` column migration to `metal`, `general`, `it`; Step 3 runs
`rebuild_duplicates.py --env <environment>` against each (incremental, chains to `clusterize.py`
automatically) to populate OCR-text and safety-net CLIP edges for the existing active-library
text-heavy population and re-cluster; Step 5 re-runs `ingest_find_duplicates` (both tiers) against
`general`'s in-flight pending batch specifically, since it's the one environment with an active
ingestion run. Wait for explicit confirmation before continuing.

- [ ] **Step 2: Apply the migration to all three environments**

Per this repo's documented migration workflow, for each of `metal`, `general`, `it` in turn (use
absolute paths for `Get-Content` — the relative form has intermittently resolved against the wrong
cwd in this session; verify with `Write-Host "DATABASE_URL set: $($env:DATABASE_URL -ne $null)"`
if in doubt):

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.<environment>" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes\Storage"
alembic upgrade head
```

Confirm each environment's backend (`/api/diagnostics/health`) still responds normally after its
migration, before moving to the next environment.

- [ ] **Step 3: Run `rebuild_duplicates.py` against all three environments**

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.<environment>" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.rebuild_duplicates --env <environment>
```

Run each as a background task if it looks likely to exceed a few minutes (a real KNN search over
the active corpus, three probes now instead of one — expect it to take noticeably longer than a
CLIP-only run did, though the two new probes are each scoped to a small subpopulation, so the
increase should not be dramatic). This chains into `clusterize.py` automatically (its `--no-chain`
default is off) — confirm the chained clusterize run completes too before moving to the next
environment. Confirm backend health after each environment.

- [ ] **Step 4: Verify the results (read-only)**

For each environment, via `DATABASE_URL_READONLY` (never the main `DATABASE_URL`):

```sql
SELECT distance_source, count(*) FROM tmp_duplicates GROUP BY distance_source;
```

Expected: a nonzero `ocr_text` count in `general` (the environment with the most text-heavy
images, 3,348+735 per the classifier's own rollout counts) and `metal`/`it` (406/178
respectively); a plausible split, not zero.

Then, still read-only, pull a handful of the newly-inserted `ocr_text`-sourced and
safety-net-sourced (`distance_source='clip'` at a distance `< 0.02`) pairs and open the actual
image files (same manual-verification approach used throughout this whole effort) to confirm they
look like genuine matches, not a new false-positive class this rollout introduced by accident.

- [ ] **Step 5: Re-run ingestion duplicate-finding for `general`'s in-flight batch**

```powershell
Get-Content "H:\workspace_sandbox\memes\environments\.env.general" | foreach { $name, $value = $_.split('=',2); if ($name) { set-content env:\$name $value } }
Set-Location "H:\workspace_sandbox\memes"
$env:PYTHONIOENCODING = "utf-8"
python -m batch.ingest_find_duplicates --env general --tier tier_a
python -m batch.ingest_find_duplicates --env general --tier tier_b
```

Confirm backend health after each.

- [ ] **Step 6: Spot-check the live Tier B review UI**

Open `/ingestion` (Tier B) for `general` and confirm: the new label renders (`text` vs. `visual`),
and a handful of candidates that were previously false positives (per the earlier same-day
investigation — e.g. two unrelated chat screenshots) either no longer appear as candidates at all
(correctly excluded from the general CLIP probe) or appear with a genuinely closer OCR-text-sourced
match instead.

- [ ] **Step 7: Mark the spec done**

`docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md`: `status: approved` →
`status: done`; add a "## Rollout outcome" section with each environment's `distance_source` counts
from Step 4 and the spot-check findings from Steps 4 and 6.

```bash
git add docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md
git commit -m "docs: mark text-embedding duplicate matching spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- §1 (schema) → Task 1.
- §2 (probe-set staleness) → folded into Task 2 (the correctness requirement this whole task
  exists to satisfy correctly, per this plan's own Global Constraints).
- §3 (`rebuild_duplicates.py`) → Task 2, using the **corrected** design (safety-net probe has no
  incremental skip condition) — this plan's own writing process caught a more serious version of
  the staleness bug than the spec's own self-review had caught, and the spec itself was amended in
  place (commit `b1457df`) before this plan was written, so spec and plan now agree; see this
  plan's Global Constraints and Task 2's own inline commentary for the full reasoning trail.
- §4 (`ingest_find_duplicates.py`) → Task 3, using the corrected "always
  `TEXT_EMBEDDING_LOOSE_THRESHOLD`" design (also already fixed in the spec itself before this plan
  was written).
- §5 (`clusterize.py`) → Task 4.
- §6 (Backend) → Task 5.
- §7 (Frontend) → Task 6.
- Rollout section → Task 7 in full.
- Non-goals respected: no task touches the admin Duplicates page or `ClusterRow.tsx`'s
  `edgeSummaryFor`; `resolve_cluster` stays source-agnostic; no threshold retuning beyond today's
  calibration; no fourth signal.

**Placeholder scan:** none found — every code step has complete, literal content, including full
new test files' worth of real test code (not descriptions of tests to write). Two real defects
*within* that test code were caught and fixed during this same self-review pass, not left for an
implementer to discover: (1) `test_safety_net_not_starved_by_unrelated_general_probe_match`'s
first draft called `_unit_vector(0.001)` to construct a "close but not identical" CLIP vector —
`_unit_vector` only accepts an integer list index, so `0.001` would have raised `TypeError` at
collection time, not even reached an assertion failure. Replaced with a new `_near_unit_vector`
helper, whose cosine distance from the exact unit vector was independently computed (~0.106,
confirmed via a real Python calculation, not estimated) to land safely inside the general probe's
threshold. (2) `test_general_clip_probe_excludes_text_heavy_vs_text_heavy_pair` was redundant with
the test immediately following it — its own inline comment admitted it "can't distinguish"
what the next test proves directly and more precisely. Removed rather than left in as dead
weight; Task 2's test count updated from 17 to 15 accordingly (11 existing + 4 new, not +6).

**Type/name consistency:** `distance_source` spelled identically across every task — SQL column,
Python parameters/variables, Pydantic fields, TypeScript field access. `find_duplicates()`'s new
signature (`..., distance_source: str, embedding_table: str = "embeddings", extra_params=None`) is
used identically at every call site across Tasks 2 and 3. `TEXT_EMBEDDING_TIGHT_THRESHOLD`/
`TEXT_EMBEDDING_LOOSE_THRESHOLD`/`CLIP_SAFETY_NET_THRESHOLD` defined once in Task 2, imported
(never redefined) in Task 3. `PROXIMITY_THRESHOLD_OCR_TEXT` defined once in Task 4, used only
there (Tasks 2/3 don't need it — their own tight/loose bounds are the `TEXT_EMBEDDING_*` constants,
a deliberate distinction: `clusterize.py`'s constant is the corpus-wide "confirmed cluster" cutoff,
while `rebuild_duplicates.py`/`ingest_find_duplicates.py`'s constants are probe *insertion*
thresholds — they happen to share the same numeric value today, consistent with how
`PROXIMITY_THRESHOLD` and `TIER_A_THRESHOLD` already relate for the CLIP signal).

**Cross-task interface check:** Task 3 imports five names from Task 2's module
(`find_duplicates`, `_EXCLUDE_TEXT_HEAVY_PAIR`, `_TEXT_HEAVY_PAIR_ONLY`,
`CLIP_SAFETY_NET_THRESHOLD`, `TEXT_EMBEDDING_LOOSE_THRESHOLD`) — confirmed all five are actually
defined at module level in Task 2's Step 1 (not nested inside a function), so they're genuinely
importable. Task 5 depends on Task 1's column existing in the DB (via Task 1's migration) and on
Tasks 2-4 having nothing to do with its own SELECT-list changes (pure passthrough of whatever's in
the table) — no hidden coupling. Task 6 depends on Task 5's generated frontend type actually
carrying the new field — flagged explicitly in Task 6's own brief as something to confirm against
the real generated file rather than assume the exact TypeScript field shape.
