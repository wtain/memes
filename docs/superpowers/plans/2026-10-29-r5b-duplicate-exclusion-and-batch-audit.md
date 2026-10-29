# R5b Duplicate Exclusion and Batch Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep a recut source/cut pair out of the duplicate candidates of `rebuild_duplicates` and `ingest_find_duplicates`, and pin the OQ-7 audit answers (`move_flagged`, `ingest_hash_dedup`) with tests.

**Architecture:** Both duplicate jobs insert every candidate through `find_duplicates()` in `batch/rebuild_duplicates.py`. One `NOT EXISTS` against `image_cuts` goes inside its lateral subquery (before `LIMIT :k`), so it covers both jobs and all three probes and an excluded pair does not use a K slot. The audit adds characterization tests only; no production code changes in `move_flagged` or `ingest_hash_dedup`.

**Tech Stack:** Python 3.11, SQLAlchemy async raw SQL, pytest (integration against `ocrdb_test`), pgvector.

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r5b-duplicate-exclusion-and-batch-audit-design.md`

## Global Constraints

- The exclusion is exactly: `NOT EXISTS (SELECT 1 FROM image_cuts c WHERE c.cut_image_id IN (probe.id, e2.image_id) AND c.source_image_id IN (probe.id, e2.image_id))`, in the `WHERE` of the lateral subquery of `find_duplicates()`, next to `AND ({corpus_filter_sql})`, as a module constant `_EXCLUDE_RECUT_PAIR` with a comment on why it sits inside the lateral. It applies to every `image_cuts` row with both ids, in any state.
- No change to `Storage/models.py`, no migration, no change to `ingest_find_duplicates.py`, `move_flagged.py` or `ingest_hash_dedup.py`.
- `find_duplicates()`'s signature and its existing behaviour for pairs that are not a recut pair do not change.
- This is shared batch code: run the **whole** `tests/integration/` root and `batch/tests/` before finishing (CLAUDE.md "Running the right test scope").
- Python is `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe` (the venv lives in the main checkout, not the worktree). Integration tests need `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`. Test roots run separately.
- Many repo files use CRLF; preserve line endings (use the Edit tool for existing files). Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never run a migration or a batch against a live metal/general/IT database.

## Review Focus

- The exclusion works whichever side is the probe (source as probe, cut as probe). Pinned in Task 1.
- It excludes only the recut pair: a cut still pairs with its source's other copies, and the source with them. Pinned in Task 1.
- An excluded pair does not consume a K slot: with `k = 1` the source still finds its next-nearest real duplicate. Pinned in Task 1.
- The ingestion fragments (`find_batch_duplicates`) go through the same clause. Pinned in Task 1 (synthetic: an ingested file is not a cut in practice, the clause is status-agnostic).
- The audit answers hold: a flagged `cut_proposed` image's file is moved by `move_flagged`, and a dropped file whose hash equals a `cut_proposed` image's is not dropped as a duplicate. Pinned in Task 2, each with a mutation check that the test can fail.

---

### Task 1: Exclude recut pairs in find_duplicates

**Files:**
- Modify: `batch/rebuild_duplicates.py`
- Test: `tests/integration/test_recut_pair_exclusion.py`

**Interfaces:**
- Consumes: `find_duplicates(session, probe_sql, corpus_filter_sql, k, threshold, distance_source, embedding_table="embeddings", extra_params=None, extra_where_sql=None) -> int`, `rebuild_active_library(session, k, threshold, full=False) -> int` (`batch/rebuild_duplicates.py`); `find_batch_duplicates(session, batch_id, k, threshold) -> int` (`batch/ingest_find_duplicates.py`); models `CutState`, `Embedding`, `Image`, `ImageCut`.
- Produces: module constant `_EXCLUDE_RECUT_PAIR` in `batch/rebuild_duplicates.py`.

- [ ] **Step 1: Write the failing tests**

`tests/integration/test_recut_pair_exclusion.py`:

```python
"""A recut source/cut pair is never a duplicate candidate (screenshot recut R5b). Requires a live PostgreSQL instance
with pgvector -- see tests/integration/conftest.py. The test database is shared, so assertions are about the seeded ids."""
import uuid

import pytest
from sqlalchemy import text

from batch.ingest_find_duplicates import find_batch_duplicates
from batch.rebuild_duplicates import find_duplicates, rebuild_active_library
from repository.batch_runs import BatchRunRepository
from Storage.models import CutState, Embedding, Image, ImageCut

_DIM = 512
_ONE_IMAGE_PROBE = """
    SELECT i.id, e.embedding FROM images i JOIN embeddings e ON e.image_id = i.id WHERE i.id = :probe_id
"""


def _vector(*values: float) -> list[float]:
    vec = [0.0] * _DIM
    for index, value in enumerate(values):
        vec[index] = value
    return vec


async def _image(session, vector, status="active", batch_id=None) -> uuid.UUID:
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(image)
    await session.flush()
    session.add(Embedding(image_id=image.id, embedding=vector))
    await session.flush()
    return image.id


async def _cut_row(session, source_id, cut_id, state=CutState.APPROVED) -> None:
    session.add(ImageCut(source_image_id=source_id, cut_image_id=cut_id, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
                         detector="margin_trim", detector_version="1", state=state))
    await session.flush()


async def _pairs(session, ids) -> set[frozenset]:
    rows = (await session.execute(text("SELECT image_id1, image_id2 FROM tmp_duplicates"))).all()
    return {frozenset((r.image_id1, r.image_id2)) for r in rows if r.image_id1 in ids or r.image_id2 in ids}


@pytest.mark.asyncio(loop_scope="session")
async def test_without_a_cut_row_identical_images_do_pair(db_session):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert await _pairs(db_session, {source, cut}) == {frozenset({source, cut})}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("state", [CutState.APPROVED, CutState.PROPOSED, CutState.REJECTED])
async def test_rebuild_never_pairs_a_source_with_its_cut(db_session, state):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, source, cut, state)
    control_a = await _image(db_session, _vector(0.0, 1.0))
    control_b = await _image(db_session, _vector(0.0, 1.0))

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert await _pairs(db_session, {source, cut, control_a, control_b}) == {frozenset({control_a, control_b})}


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("probe", ["source", "cut"])
async def test_the_pair_is_excluded_whichever_side_is_the_probe(db_session, probe):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, source, cut)

    await find_duplicates(db_session, _ONE_IMAGE_PROBE, "i2.status = 'active'", k=20, threshold=0.3,
                          distance_source="clip", extra_params={"probe_id": source if probe == "source" else cut})

    assert await _pairs(db_session, {source, cut}) == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_the_source_and_the_cut_still_pair_with_other_copies(db_session):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))
    copy = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, source, cut)

    await rebuild_active_library(db_session, k=20, threshold=0.3)

    pairs = await _pairs(db_session, {source, cut, copy})
    assert frozenset({source, cut}) not in pairs
    assert frozenset({source, copy}) in pairs or frozenset({cut, copy}) in pairs
    assert len(pairs) == 2  # the copy pairs with each of them; k=20 leaves room for both


@pytest.mark.asyncio(loop_scope="session")
async def test_an_excluded_pair_does_not_use_up_a_k_slot(db_session):
    source = await _image(db_session, _vector(1.0))
    cut = await _image(db_session, _vector(1.0))            # distance 0 from the source, the nearest neighbour
    other = await _image(db_session, _vector(1.0, 0.3))     # distance ~0.04, the next nearest
    await _cut_row(db_session, source, cut)

    await find_duplicates(db_session, _ONE_IMAGE_PROBE, "i2.status = 'active'", k=1, threshold=0.3,
                          distance_source="clip", extra_params={"probe_id": source})

    assert await _pairs(db_session, {source, cut, other}) == {frozenset({source, other})}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_ingestion_fragments_use_the_same_exclusion(db_session):
    # Synthetic: an ingested file is not a cut in practice. The clause is status-agnostic, and this pins that
    # find_batch_duplicates reaches it through find_duplicates().
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    pending = await _image(db_session, _vector(1.0), "pending", batch_id)
    active = await _image(db_session, _vector(1.0))
    await _cut_row(db_session, active, pending, CutState.PROPOSED)
    control_pending = await _image(db_session, _vector(0.0, 1.0), "pending", batch_id)
    control_active = await _image(db_session, _vector(0.0, 1.0))

    await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert await _pairs(db_session, {pending, active, control_pending, control_active}) == {
        frozenset({control_pending, control_active})}
```

(`test_the_source_and_the_cut_still_pair_with_other_copies`: with three identical images, the general probe's KNN pairs each probe with its neighbours, so the copy pairs with the source and with the cut, and the source/cut pair is the only one missing. If the test database holds other active images with a vector near `(1.0)`, assert on the seeded ids only, as above.)

- [ ] **Step 2: Run to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/integration/test_recut_pair_exclusion.py -v`
Expected: `test_without_a_cut_row_identical_images_do_pair` passes (it proves the premise); the others fail because the source/cut pair is inserted (`{frozenset({source, cut}), ...} != ...`, and for the K-slot test `{frozenset({source, cut})} != {frozenset({source, other})}`).

- [ ] **Step 3: Implement**

In `batch/rebuild_duplicates.py`, after `_TEXT_HEAVY_PAIR_ONLY` add:

```python
# A recut source and its cut are the same meme, so they must never be each other's duplicate candidates (screenshot recut
# R5b, FR-11). After an approve the flagged source stays `active` until move_flagged and unregister_deleted_images have
# run, so both are in the probed corpus for a while. Applies to every image_cuts row with both ids, whatever its state:
# a proposed or rejected cut is not in a probed corpus anyway. Lives inside the LATERAL, before LIMIT :k, because the cut
# is the source's nearest neighbour; filtering afterwards would spend a K slot on a pair that is then thrown away.
# image_cuts.cut_image_id has a unique index, so this is one index lookup per scanned candidate. References probe.id and
# e2.image_id, both in scope where find_duplicates() interpolates it.
_EXCLUDE_RECUT_PAIR = """
    NOT EXISTS (
        SELECT 1 FROM image_cuts c
        WHERE c.cut_image_id IN (probe.id, e2.image_id)
          AND c.source_image_id IN (probe.id, e2.image_id)
    )
"""
```

and in `find_duplicates()` change the lateral's

```
            WHERE e2.image_id != probe.id
              AND ({corpus_filter_sql})
```

to

```
            WHERE e2.image_id != probe.id
              AND ({corpus_filter_sql})
              AND ({_EXCLUDE_RECUT_PAIR})
```

Add one sentence to the `find_duplicates` docstring: "A recut source/cut pair is never inserted (`_EXCLUDE_RECUT_PAIR`)."

- [ ] **Step 4: Run to verify it passes**

Run the Step 2 command. Expected: all pass.

- [ ] **Step 5: Run the shared-code scopes, then commit**

Run: `DATABASE_URL=... python.exe -m pytest tests/integration/ -q` and `python.exe -m pytest batch/tests/ -q`.
Expected: all pass (899 + 8 new in integration; batch unchanged).

```bash
git add batch/rebuild_duplicates.py tests/integration/test_recut_pair_exclusion.py
git commit -m "feat(recut): R5b keep source/cut pairs out of duplicate candidates

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Pin the batch audit answers

**Files:**
- Create: `tests/integration/test_move_flagged_cut_proposed.py`
- Modify (append a test): `tests/integration/test_ingest_hash_dedup.py`

**Interfaces:**
- Consumes: `batch.move_flagged.run(session, base_path) -> SimpleMetricsListener` (`counters_dict()` holds `moved`), `ImageExtrasRepository.set_flagged(image_id, flagged, remarks)`, `batch.ingest_hash_dedup.dedupe_cross_corpus(session, source_path, survivors, duplicates_dir) -> dict[str, str]`, `ImageStatus`.
- Produces: nothing (characterization tests of existing behaviour; they are expected to pass on the first run, so each gets a mutation check).

- [ ] **Step 1: Write the tests**

`tests/integration/test_move_flagged_cut_proposed.py`:

```python
"""move_flagged moves a flagged image whatever its status, so a rejected recut cut (cut_proposed, flagged) is moved to
excluded/ like any other (screenshot recut R5b, OQ-7 audit). Requires a live PostgreSQL instance."""
import uuid

import pytest

import batch.move_flagged as move_flagged
from repository.image_extras import ImageExtrasRepository
from Storage.models import Image, ImageStatus


@pytest.mark.asyncio(loop_scope="session")
async def test_move_flagged_moves_a_flagged_cut_proposed_image(db_session, tmp_path):
    cut = Image(filename=f"{uuid.uuid4()}.jpg", status=ImageStatus.CUT_PROPOSED)
    db_session.add(cut)
    await db_session.flush()
    (tmp_path / cut.filename).write_bytes(b"x")
    await ImageExtrasRepository(db_session).set_flagged(cut.id, True, "recut_rejected")

    metrics = await move_flagged.run(db_session, str(tmp_path))

    assert not (tmp_path / cut.filename).exists()
    assert (tmp_path / "excluded" / cut.filename).exists()
    assert metrics.counters_dict().get("moved", 0) >= 1
```

Append to `tests/integration/test_ingest_hash_dedup.py`, after `test_dedupe_cross_corpus_ignores_pending_images`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_dedupe_cross_corpus_ignores_cut_proposed_images(tmp_path, db_session):
    """A recut cut is not in the library yet: a dropped file with the same hash is not a duplicate of it (R5b audit)."""
    db_session.add(Image(filename="cut.jpg", status="cut_proposed", content_hash="abc123"))
    await db_session.flush()

    _write(tmp_path, "new.jpg", b"whatever")
    survivors = {"new.jpg": "abc123"}
    duplicates_dir = str(tmp_path / "duplicates")

    remaining = await dedupe_cross_corpus(db_session, str(tmp_path), survivors, duplicates_dir)

    assert remaining == {"new.jpg": "abc123"}
```

- [ ] **Step 2: Run them**

Run: `DATABASE_URL=... python.exe -m pytest tests/integration/test_move_flagged_cut_proposed.py tests/integration/test_ingest_hash_dedup.py -v`
Expected: all pass on the first run (they describe existing behaviour).

- [ ] **Step 3: Mutation check, so the tests can fail**

Temporarily change `batch/move_flagged.py`'s query by adding `.where(Image.status == "active")` after the `ImageExtras.flagged == True` clause: `test_move_flagged_moves_a_flagged_cut_proposed_image` must fail. Revert it. Temporarily change `batch/ingest_hash_dedup.py`'s `Image.status == "active"` in `dedupe_cross_corpus` to `Image.status != "pending"`: `test_dedupe_cross_corpus_ignores_cut_proposed_images` must fail. Revert it. Confirm with `git diff --stat batch/` that no production file under `batch/` is modified.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_move_flagged_cut_proposed.py tests/integration/test_ingest_hash_dedup.py
git commit -m "test(recut): R5b pin the move_flagged and ingest_hash_dedup audit answers

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Docs, status and full verification

**Files:**
- Modify: `docs/superpowers/specs/2026-10-17-screenshot-recut/requirements.md`, `docs/batches/use-cases/duplicates-and-clusters.md`, `CLAUDE.md`, `docs/image-derived-data.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r5b-duplicate-exclusion-and-batch-audit-design.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`

- [ ] **Step 1: Edit the docs**

- `requirements.md`, the OQ-7 row: replace "R5 audit" in the last column with "answered in the R5b design (`2026-10-29-r5b-duplicate-exclusion-and-batch-audit-design.md`): `move_flagged` and `ingest_hash_dedup` need no change; `unregister_deleted_images` was extended in R5a".
- `docs/batches/use-cases/duplicates-and-clusters.md`: after the paragraph that ends "`--full` clears the active-library pairs and re-probes everything." add: "A recut source and its cut (an `image_cuts` row with both ids) are never inserted as a candidate pair, whichever probe finds them (screenshot recut R5b)."
- `CLAUDE.md`: in the `rebuild_duplicates` entry, after "...thresholds are fixed module constants)." add "A recut source/cut pair (an `image_cuts` row with both ids) is never inserted as a candidate, in either duplicate job (`_EXCLUDE_RECUT_PAIR` in `find_duplicates`)." Keep the entry's wrapping style.
- `docs/image-derived-data.md`: in the `tmp_duplicates` row change "(source/cut pairs are excluded, screenshot-recut R5)" to "(source/cut pairs are excluded by `find_duplicates`, screenshot-recut R5b)".
- R5b spec: `Status: draft` becomes `Status: implementation`, and add `Plan: docs/superpowers/plans/2026-10-29-r5b-duplicate-exclusion-and-batch-audit.md` under the status line. Tracker: R5b row status `implementation` with the plan path added.

- [ ] **Step 2: Full verification**

Run each root separately: `DATABASE_URL=... python.exe -m pytest tests/integration/ -q`; `python.exe -m pytest batch/tests/ -q`; `python.exe -m pytest tests/rules/ -q`; `python.exe -m pytest tests/docs/ -q`; from `Backend/`: `python.exe -m pytest -q` (known unrelated failure: `tests/test_batch_subprocess.py::test_forces_utf8_output_encoding_in_child_env`, report it by name). No frontend, schema or API change, so the frontend gate and type regeneration are not needed.
Expected: all pass except the one known Backend failure.

- [ ] **Step 3: Commit**

```bash
git add docs CLAUDE.md
git commit -m "docs(recut): R5b implementation status

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
