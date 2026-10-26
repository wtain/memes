# R3b: Derive group 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the existing pixel-derived jobs run on `cut_proposed` images (`--status cut_proposed` for three jobs), keep cut and pending vectors out of the concept rankings, and document the run order.

**Architecture:** Three argparse `choices` lists gain `cut_proposed`; the selection query of `build_image_embeddings` is extracted into a testable function; `ConceptRepositoryBase.top_images_for_concept` filters `Image.status == 'active'`; tests pin the duplicate probes; docs and a docs guard follow.

**Tech Stack:** Python 3.11 (`.venv311`), SQLAlchemy async, pytest (separate roots), PostgreSQL + pgvector (`ocrdb_test`).

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3b-derive-group-2-design.md`

## Global Constraints

- `--status` choices: `build_image_embeddings` `{pending, active, cut_proposed, all}`; `classify_text_heavy` and `build_ocr_text_embeddings` `{active, pending, cut_proposed}`. Defaults stay `active`.
- The admin trigger is unchanged (always `--status active`).
- YOLO and CONCEPT tag jobs are NOT extended.
- Repositories never commit. Test roots run separately; `tests/integration/` needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`; integration tests use `@pytest.mark.asyncio(loop_scope="session")` and the `db_session` fixture.
- Files may use CRLF: edit with the Edit tool, never round-trip through PowerShell Get/Set-Content.
- Commits end with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

- `--status all` of `build_image_embeddings` still selects every status (including `cut_proposed`): pinned in Task 2.
- `--incremental` with `cut_proposed` must not re-embed a cut that already has a vector: Task 2.
- A `pending` image with a vector must stop taking concept top-N slots (intended side effect): Task 3.
- A `cut_proposed` image with a vector must never be a duplicate candidate: Task 4.
- A script whose `--status` choices drift away from `ImageStatus.ALL` plus `all`: Task 1 guard.

---

### Task 1: `--status cut_proposed` for the two OCR-based jobs, and the choices guard

**Files:**
- Create: `tests/docs/test_status_flag_choices.py`
- Modify: `batch/classify_text_heavy.py` (line 73 `choices`, docstring), `batch/build_ocr_text_embeddings.py` (line 78 `choices`, docstring)
- Test: `tests/integration/test_classify_text_heavy.py`, `tests/integration/test_build_ocr_text_embeddings.py` (append)

**Interfaces:**
- Consumes: `ImageStatus.ALL` from `Storage.models`.
- Produces: `status_choices(path) -> list[str] | None` (test-only helper in the guard); CLI choice `cut_proposed` on both jobs. `run(...)` signatures unchanged.

- [ ] **Step 1: Write the failing guard** in `tests/docs/test_status_flag_choices.py`:

```python
"""Every batch script's --status choices must be real image statuses (plus the selector 'all'), and the jobs that derive
data for recut cuts must accept cut_proposed (screenshot-recut R3b)."""
import ast
from pathlib import Path

from Storage.models import ImageStatus

BATCH = Path(__file__).resolve().parents[2] / "batch"
TAKES_CUT_PROPOSED = {"build_image_embeddings", "classify_text_heavy", "build_ocr_text_embeddings"}


def status_choices(path: Path):
    """The literal choices= list of the script's --status argument, or None when it has none."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "--status"):
            for keyword in node.keywords:
                if keyword.arg == "choices":
                    return list(ast.literal_eval(keyword.value))
            return None
    return None


def test_every_status_choice_is_an_image_status_or_all():
    allowed = set(ImageStatus.ALL) | {"all"}
    checked = 0
    for path in sorted(BATCH.glob("*.py")):
        choices = status_choices(path)
        if choices is not None:
            checked += 1
            assert set(choices) <= allowed, (path.name, choices)
    assert checked, "no --status choices found; the pattern or the folder moved"


def test_the_derivation_jobs_accept_cut_proposed():
    for name in sorted(TAKES_CUT_PROPOSED):
        assert "cut_proposed" in (status_choices(BATCH / f"{name}.py") or []), name
```

- [ ] **Step 2: Run it, expect RED**

Run: `.venv311/Scripts/python -m pytest tests/docs/test_status_flag_choices.py -v`
Expected: `test_the_derivation_jobs_accept_cut_proposed` FAILS (all three names lack `cut_proposed`); the first test passes.

- [ ] **Step 3: Write the behaviour tests for the two jobs.** They exercise `run(...)` with `"cut_proposed"` (the repository already takes the status as a parameter, so these pin behaviour and are expected to pass at once; say so in the ledger).

Append to `tests/integration/test_classify_text_heavy.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_cut_proposed_status_classifies_only_cuts(tmp_path, db_session):
    cut = Image(filename="cut.jpg", status="cut_proposed", width=200, height=200)
    other = Image(filename="other.jpg", status="active", width=200, height=200)
    db_session.add_all([cut, other])
    await db_session.flush()
    for name, image in (("cut.jpg", cut), ("other.jpg", other)):
        bboxes = _screenshot_image(os.path.join(str(tmp_path), name))
        for bbox in bboxes:
            db_session.add(OCRText(image_id=image.id, bbox=bbox, confidence=0.9, text="x"))
    await db_session.flush()

    metrics = await run(db_session, str(tmp_path), CONFIDENCE_MIN, "cut_proposed")
    await db_session.commit()

    assert metrics.counters_dict() == {TEXT_HEAVY: 1}
    classified = (await db_session.execute(select(ImageClassification.image_id))).scalars().all()
    assert cut.id in classified and other.id not in classified
```

Append to `tests/integration/test_build_ocr_text_embeddings.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_cut_proposed_status_embeds_only_cuts(db_session):
    cut = await _text_heavy_image(db_session, status="cut_proposed")
    other = await _text_heavy_image(db_session, status="active")
    for image in (cut, other):
        db_session.add(OCRText(image_id=image.id, text="a real sentence to embed", confidence=0.9, lang_score=0.9))
    await db_session.flush()

    metrics = await run(db_session, CONFIDENCE_MIN, LANG_SCORE_MIN, "cut_proposed")
    await db_session.commit()

    assert metrics.counters_dict() == {"embedded": 1}
    embedded = (await db_session.execute(select(OCRTextEmbedding.image_id))).scalars().all()
    assert cut.id in embedded and other.id not in embedded
```

Note: the integration DB is shared across tests; if `counters_dict()` equality fails because earlier committed rows exist, scope the assertion to the two ids (as in the last line) and drop the counter assertion. Rule it in the ledger.

- [ ] **Step 4: Run them**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_classify_text_heavy.py tests/integration/test_build_ocr_text_embeddings.py -v`
Expected: PASS (pins existing behaviour).

- [ ] **Step 5: Implement.** In `batch/classify_text_heavy.py` and `batch/build_ocr_text_embeddings.py` change the argument to `parser.add_argument("--status", choices=["active", "pending", "cut_proposed"], default="active")` and add one docstring sentence to each: "`--status cut_proposed` covers recut cuts (run after their OCR is refreshed, see docs/image-status.md)."

- [ ] **Step 6: Run the guard, expect GREEN for the two jobs**

Run: `.venv311/Scripts/python -m pytest tests/docs/test_status_flag_choices.py -v`
Expected: `test_the_derivation_jobs_accept_cut_proposed` still FAILS, only for `build_image_embeddings` (Task 2). Confirm the failure names only that job.

- [ ] **Step 7: Commit**

```bash
git add tests/docs/test_status_flag_choices.py tests/integration/test_classify_text_heavy.py tests/integration/test_build_ocr_text_embeddings.py batch/classify_text_heavy.py batch/build_ocr_text_embeddings.py
git commit -m "feat(recut): classify_text_heavy and build_ocr_text_embeddings accept --status cut_proposed"
```

(End the message with the Co-Authored-By line.)

---

### Task 2: `build_image_embeddings --status cut_proposed`

**Files:**
- Modify: `batch/build_image_embeddings.py`
- Create: `tests/integration/test_build_image_embeddings.py`

**Interfaces:**
- Produces: `async def select_images_to_embed(session, incremental: bool, target_status: str) -> list[Row]`, rows of `(filename, id)`, extracted from `_process`; `_process` calls it. CLI `--status` choices `["pending", "active", "cut_proposed", "all"]`.

- [ ] **Step 1: Write the failing tests** in `tests/integration/test_build_image_embeddings.py`:

```python
"""Selection of the images build_image_embeddings works on (the model itself is not exercised)."""
import uuid

import pytest

from batch.build_image_embeddings import select_images_to_embed
from Storage.models import Embedding, Image


async def _image(session, status, vector=False):
    image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
    session.add(image)
    await session.flush()
    if vector:
        session.add(Embedding(image_id=image.id, embedding=[0.1] * 512))
        await session.flush()
    return image.id


async def _ids(session, incremental, status):
    return {row.id for row in await select_images_to_embed(session, incremental, status)}


@pytest.mark.asyncio(loop_scope="session")
async def test_cut_proposed_selects_only_cuts(db_session):
    cut, active, pending = (await _image(db_session, s) for s in ("cut_proposed", "active", "pending"))
    ids = await _ids(db_session, False, "cut_proposed")
    assert cut in ids and active not in ids and pending not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_incremental_skips_cuts_that_already_have_a_vector(db_session):
    fresh, done = await _image(db_session, "cut_proposed"), await _image(db_session, "cut_proposed", vector=True)
    ids = await _ids(db_session, True, "cut_proposed")
    assert fresh in ids and done not in ids


@pytest.mark.asyncio(loop_scope="session")
async def test_active_and_pending_runs_ignore_cuts(db_session):
    cut = await _image(db_session, "cut_proposed")
    assert cut not in await _ids(db_session, True, "active")
    assert cut not in await _ids(db_session, True, "pending")


@pytest.mark.asyncio(loop_scope="session")
async def test_all_selects_every_status(db_session):
    made = [await _image(db_session, s) for s in ("cut_proposed", "active", "pending")]
    assert set(made) <= await _ids(db_session, True, "all")
```

- [ ] **Step 2: Run, expect RED**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/test_build_image_embeddings.py -v`
Expected: collection error / ImportError `select_images_to_embed` (the function is missing).

- [ ] **Step 3: Implement.** In `batch/build_image_embeddings.py` add above `_process`:

```python
def _status_filter(target_status: str):
    return () if target_status == "all" else (Img.status == target_status,)


async def select_images_to_embed(session, incremental: bool, target_status: str):
    """(filename, id) rows of the images to embed: every image of the status, or with `incremental` only those that
    have no vector yet."""
    status_filter = _status_filter(target_status)
    stmt = select(Img.filename, Img.id).where(*status_filter)
    if incremental:
        has_embedding = select(Embedding.image_id).distinct().scalar_subquery()
        stmt = stmt.where(Img.id.not_in(has_embedding))
    return (await session.execute(stmt)).all()
```

In `_process` replace the `status_filter = ...` line with `status_filter = _status_filter(target_status)` and replace the `if incremental: ... else: ...` block plus `rows = (await session.execute(stmt)).all()` with `rows = await select_images_to_embed(session, incremental, target_status)`. Change `--status` to `choices=["pending", "active", "cut_proposed", "all"]`, and update the module docstring to `--status {pending,active,cut_proposed,all}` plus one sentence: "cuts: `--status cut_proposed --incremental`".

- [ ] **Step 4: Run, expect GREEN, plus the guard**

Run: the Step 2 command, then `.venv311/Scripts/python -m pytest tests/docs/test_status_flag_choices.py -v`
Expected: 4 passed; guard 2 passed.

- [ ] **Step 5: Commit** (`feat(recut): build_image_embeddings accepts --status cut_proposed`, with `git add batch/build_image_embeddings.py tests/integration/test_build_image_embeddings.py`).

---

### Task 3: Concept rankings only consider active images

**Files:**
- Modify: `repository/concepts.py` (`image_embeddings_subq`, around line 31-39)
- Test: `tests/integration/test_concepts_repository.py` (append; add imports `ConceptImage, ConceptImageSet, Embedding, Image` to the `Storage.models` import)

**Interfaces:**
- Consumes: `ConceptsRepository(session).top_images_for_concept(concept_id, threshold=0.2, limit=50)` returning rows with `.image_id`.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_top_images_for_concept_only_ranks_active_images(db_session):
    vec = [0.0] * 512
    vec[0] = 1.0
    concept = Concept(name=f"test-{uuid.uuid4()}")
    db_session.add(concept)
    await db_session.flush()
    image_set = ConceptImageSet(concept_id=concept.id, name="s")
    db_session.add(image_set)
    await db_session.flush()
    db_session.add(ConceptImage(concept_image_set_id=image_set.id, filename="ref.jpg", embedding=vec))
    ids = {}
    for status in ("active", "pending", "cut_proposed"):
        image = Image(filename=f"{uuid.uuid4()}.jpg", status=status)
        db_session.add(image)
        await db_session.flush()
        db_session.add(Embedding(image_id=image.id, embedding=vec))  # identical to the reference: the nearest match
        ids[status] = image.id
    await db_session.flush()

    rows = await ConceptsRepository(db_session).top_images_for_concept(concept.id, threshold=0.5, limit=50)

    found = {r.image_id for r in rows}
    assert ids["active"] in found
    assert ids["pending"] not in found and ids["cut_proposed"] not in found
```

- [ ] **Step 2: Run, expect RED**

Run: `DATABASE_URL=... .venv311/Scripts/python -m pytest tests/integration/test_concepts_repository.py::test_top_images_for_concept_only_ranks_active_images -v`
Expected: FAIL on `ids["pending"] not in found`.

- [ ] **Step 3: Implement.** In `repository/concepts.py` add `.where(i.status == "active")` to `image_embeddings_subq` (after the `.join(e, ...)`). Use `ImageStatus.ACTIVE` if `ImageStatus` is already importable there without a cycle; otherwise the literal, matching the file.

- [ ] **Step 4: Run, expect GREEN; then the concept-related files**

Run: the Step 2 command, then `... pytest tests/integration/test_concepts_repository.py tests/integration/test_backend_concept_repository.py -v`
Expected: all pass.

- [ ] **Step 5: Commit** (`fix(concepts): rank only active images, so cut and pending vectors do not take concept slots`).

---

### Task 4: Pin that duplicate probes ignore cuts

**Files:**
- Test: `tests/integration/test_rebuild_duplicates.py`, `tests/integration/test_ingest_find_duplicates.py` (append)

**Interfaces:**
- Consumes: `_insert_image_with_embedding(session, vector, status)` and `rebuild_active_library(session, k, threshold)` (rebuild test); `_insert_image(session, vector, status, batch_id)` and `find_batch_duplicates(session, batch_id, k, threshold)` (ingest test).

These pin existing behaviour and pass at once (the spec says no code change); record that in the ledger. Mutation check: temporarily widen one probe to `status IN ('active','cut_proposed')` and confirm a pin fails, then revert.

- [ ] **Step 1: Append to `test_rebuild_duplicates.py`**

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_rebuild_ignores_cut_proposed_images(db_session):
    await _insert_image_with_embedding(db_session, _unit_vector(0), status="active")
    await _insert_image_with_embedding(db_session, _unit_vector(0), status="cut_proposed")

    inserted = await rebuild_active_library(db_session, k=20, threshold=0.3)

    assert inserted == 0
```

- [ ] **Step 2: Append to `test_ingest_find_duplicates.py`**

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_ingestion_probe_ignores_cut_proposed_images(db_session):
    batch_id = await BatchRunRepository(db_session).create_run(kind="ingestion", trigger="manual", stage="hash_dedup")
    await _insert_image(db_session, _unit_vector(0), "pending", batch_id)
    await _insert_image(db_session, _unit_vector(0), "cut_proposed")

    inserted = await find_batch_duplicates(db_session, batch_id, k=20, threshold=0.3)

    assert inserted == 0
```

- [ ] **Step 3: Run**

Run: `DATABASE_URL=... .venv311/Scripts/python -m pytest tests/integration/test_rebuild_duplicates.py tests/integration/test_ingest_find_duplicates.py -v`
Expected: all PASS.

- [ ] **Step 4: Mutation check.** Find the status filters (`grep -n "status" batch/rebuild_duplicates.py batch/ingest_find_duplicates.py`), temporarily let `cut_proposed` through one, confirm the matching new test fails, `git checkout` the batch file.

- [ ] **Step 5: Commit** (`test(recut): duplicate probes never see cut_proposed images`).

---

### Task 5: Documentation, then the whole roots

**Files:**
- Modify: `docs/image-status.md`, `docs/image-derived-data.md`, `docs/batches/inventory.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-26-r3b-derive-group-2-design.md` (status), `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md` (R3b row)

- [ ] **Step 1: `docs/image-status.md`** "Accepted by `--status`": replace the sentence "Today every one accepts `active` (the default) and `pending`; none accepts `cut_proposed` yet (screenshot-recut R3 adds it)." with: "Every one accepts `active` (the default) and `pending`; `build_image_embeddings`, `classify_text_heavy` and `build_ocr_text_embeddings` also accept `cut_proposed` (screenshot-recut R3b). Run order for cuts: `build_image_embeddings --status cut_proposed --incremental`; then, after the cut's OCR is refreshed (R3c), `classify_text_heavy --status cut_proposed`; then `build_ocr_text_embeddings --status cut_proposed`. The last two skip images that already have a row, so a run before the OCR refresh leaves rows computed from the source's text." Keep the bullet list lines intact (the guard parses them).

- [ ] **Step 2: `docs/image-derived-data.md`**: row `ocr_text_embeddings` becomes `build_ocr_text_embeddings --status cut_proposed`, after OCR refresh and classification; `YOLO` and `CONCEPT` rows say "Not copied; not derived at proposal time, the next full `detect_entities_and_tag` / `tag_images_from_concepts` run covers the cut once it is `active`". Keep each table's first-cell format (the inventory guard parses it).

- [ ] **Step 3: `docs/batches/inventory.md`** line 55 area and `CLAUDE.md`: add the new `--status` values and the run order to the entries of `build_image_embeddings`, `classify_text_heavy`, `build_ocr_text_embeddings` (CLAUDE.md: also change the `create_recut_cuts` entry's "group 2 is left for the existing jobs with --status cut_proposed, R3b" to name the three jobs and the run order). Mention that `top_images_for_concept` ranks only active images in the `tag_images_from_concepts` entry if one exists.

- [ ] **Step 4: Spec and tracker.** Set the R3b spec status to `planned` with `Plan: docs/superpowers/plans/2026-10-26-r3b-derive-group-2.md`; tracker row R3b to `plan`/`implementation` per the tracker's own vocabulary; after the tests below pass, spec status `implementation`.

- [ ] **Step 5: Run every root, separately**

```
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" .venv311/Scripts/python -m pytest tests/integration/ -q
.venv311/Scripts/python -m pytest batch/tests/ -q
.venv311/Scripts/python -m pytest tests/docs/ -q
.venv311/Scripts/python -m pytest tests/rules/ -q
```
(`Backend/` tests: `cd Backend && ../.venv311/Scripts/python -m pytest -q`, because the concept repository is shared.)
Expected: all green; any pre-existing failure is reported by name.

- [ ] **Step 6: Commit** (`docs(recut): R3b run order, --status cut_proposed, concept guard`).

---

## Self-review

- Spec coverage: decision 2 (Tasks 1, 2), decision 3 (Task 3), decision 4 and docs (Task 5), duplicate pins (Task 4), choices guard (Task 1), whole roots (Task 5). Decision 1 needs no code; docs in Task 5.
- Names: `select_images_to_embed(session, incremental, target_status)` is the same in Task 2's tests and implementation; `status_choices` appears only in Task 1.
