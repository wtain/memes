# Duplicate-Matching Coverage-Gap Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface three read-only pipeline-coverage-gap counts (`ocr_missing_text_heavy_classification`, `text_heavy_missing_embeddings`, `embeddings_missing_lemmas`) on the existing `/api/diagnostics/statistics` endpoint and Statistics page, so an operator can see when `classify_text_heavy`/`build_ocr_text_embeddings`/`build_ocr_lemmas` have drifted out of sync — specifically closing the blind spot where an image missing `ocr_lemmas` looks identical, from the outside, to one that genuinely has no lexical overlap with its duplicate candidate.

**Architecture:** Three new anti-join scalar-subquery counts added to `DiagnosticsRepository.get_statistics()`, following that method's existing `without_tags` pattern exactly. Plumbed through the existing (hand-written, not schema-generated) `MemeStats` response model in `Backend/app/api/diagnostics.py`, and rendered as three more cells in `StatisticsPage.tsx`'s existing "Pipeline coverage" section. No new endpoint, no new DB table or column, no new UI page.

**Tech Stack:** FastAPI + SQLAlchemy async ORM (Backend), React + TypeScript (Frontend), pytest (`tests/integration/` for real-DB repository tests, `Backend/tests/` for mocked-session API tests).

**Spec:** `docs/superpowers/specs/2026-09-23-duplicate-matching-coverage-gap-diagnostics.md`

## Global Constraints

- All three counts are scoped to `Image.status == "active"` only — never `pending` or `rejected` (spec Goal / Non-goals).
- Pure read-only change: no new migration, no new table/column, no write path touched anywhere in this plan.
- **Landmine, confirmed by inspection (spec §3):** `HealthResponse`/`MemeStats`/`ContentStats`/`TrendsStats`/`StatisticsResponse` in `Backend/app/api/diagnostics.py` are hand-written `pydantic.BaseModel` classes — `response_model=StatisticsResponse` in that file resolves to the hand-written class, **not** an import from `Backend/app/types/generated/`. Updating `shared/schemas/statisticsmemestats.schema.json` and regenerating the Python tree does **not** by itself change what the live endpoint returns — Pydantic silently strips any field the hand-written class doesn't declare. `MemeStats` in `diagnostics.py` must be edited directly (Task 3).
- No alerting, thresholds, or severity styling — these are plain informational counts (spec Non-goals).
- No per-image drill-down, no new endpoint, no new frontend route (spec Non-goals).
- Any live-database read against a real `metal`/`general`/`it` environment (Task 5) uses `DATABASE_URL_READONLY` only, run directly by the controller — never handed to a subagent, and never `DATABASE_URL` (this repo's own live-DB-access rule in CLAUDE.md, following a real 2026-08-05 incident).

---

### Task 1: Shared schema + regenerate all three type trees

**Files:**
- Modify: `shared/schemas/statisticsmemestats.schema.json`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`
- Regenerate: `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`
- Regenerate: `Backend/app/types/generated/` (whichever files `datamodel-codegen` touches for `StatisticsMemeStats`)

**Interfaces:**
- Produces: three new optional-looking-but-required integer fields on the generated `StatisticsMemeStats`/`StatisticsResponse` TypeScript, Kotlin, and Python types: `ocr_missing_text_heavy_classification`, `text_heavy_missing_embeddings`, `embeddings_missing_lemmas`. Task 3's hand-written `MemeStats` class (not generated) is what Task 4's frontend page actually receives data from at runtime — this task only makes the *documented* contract and the generated Python/TS/Kotlin types match it going forward.

- [ ] **Step 1: Edit the schema file**

In `shared/schemas/statisticsmemestats.schema.json`, add three properties to the `properties` object (after `duplicate_clusters`) and three names to `required`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "statisticsmemestats.schema.json",
  "title": "StatisticsMemeStats",
  "type": "object",
  "properties": {
    "total":              { "type": "integer", "description": "Total active (visible) images" },
    "pending":            { "type": "integer", "description": "Images awaiting ingestion review (status=pending), not yet visible in browse/search" },
    "rejected":           { "type": "integer", "description": "Images rejected during ingestion review (status=rejected) as confirmed duplicates" },
    "with_embeddings":    { "type": "integer", "description": "Images with a CLIP embedding" },
    "with_ocr":           { "type": "integer", "description": "Images with at least one OCR text block" },
    "with_tags":          { "type": "integer", "description": "Images with at least one tag (any source)" },
    "without_tags":       { "type": "integer", "description": "Images with no tags" },
    "with_descriptions":  { "type": "integer", "description": "Images with an Ollama description" },
    "with_concept_tags":  { "type": "integer", "description": "Images with at least one CONCEPT-source tag" },
    "flagged":            { "type": "integer", "description": "Images marked as flagged" },
    "duplicate_clusters": { "type": "integer", "description": "Total distinct duplicate-image clusters" },
    "ocr_missing_text_heavy_classification": { "type": "integer", "description": "Active images with OCR text but no classify_text_heavy verdict yet" },
    "text_heavy_missing_embeddings":         { "type": "integer", "description": "Active images classified text_heavy with no ocr_text_embeddings row yet" },
    "embeddings_missing_lemmas":             { "type": "integer", "description": "Active images with an ocr_text_embeddings row but no ocr_lemmas rows yet -- the corroboration gate's blind spot, see docs/superpowers/specs/2026-09-19-ocr-lemma-overlap-corroboration.md" }
  },
  "required": ["total", "pending", "rejected", "with_embeddings", "with_ocr", "with_tags", "without_tags", "with_descriptions", "with_concept_tags", "flagged", "duplicate_clusters", "ocr_missing_text_heavy_classification", "text_heavy_missing_embeddings", "embeddings_missing_lemmas"]
}
```

- [ ] **Step 2: Regenerate the TypeScript tree**

Run (from `Frontend/`, matching the script's own relative paths):
```bash
cd Frontend && bash generate-types.sh
```
Expected: `memes-frontend/src/types/generated/all.d.ts` changes — the `StatisticsMemeStats` interface gains the three new `number` fields.

- [ ] **Step 3: Regenerate the Kotlin tree**

Run (from repo root):
```bash
python AndroidClient/scripts/generate_dtos.py
```
Expected: `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` changes — its `StatisticsMemeStats` (or equivalent generated) data class gains the three new fields.

- [ ] **Step 4: Regenerate the Python tree**

Run (from `Backend/`, per `documents/generation.md`):
```bash
cd Backend && datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
```
Expected: the generated `StatisticsMemeStats` Pydantic class under `Backend/app/types/generated/` gains the three new fields. **This alone does not change the live endpoint** — see Global Constraints; Task 3 does that.

- [ ] **Step 5: Verify the diffs are exactly what's expected**

Run:
```bash
git status --short
git diff -- shared/schemas/statisticsmemestats.schema.json Frontend/memes-frontend/src/types/generated/all.d.ts AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt Backend/app/types/generated/
```
Expected: only the three files/trees from Steps 1-4 changed, each showing exactly the three new fields added (no unrelated reformatting — if `json2ts`/`datamodel-codegen`/the Android script reformat unrelated parts of the output on every run, that's pre-existing tool behavior, not something this task introduces; confirm nothing else in the diff looks surprising before moving on).

- [ ] **Step 6: Commit**

```bash
git add shared/schemas/statisticsmemestats.schema.json Frontend/memes-frontend/src/types/generated/all.d.ts AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt Backend/app/types/generated/
git commit -m "feat: add coverage-gap fields to StatisticsMemeStats schema and regenerate types"
```

---

### Task 2: Repository — three coverage-gap counts

**Files:**
- Modify: `Backend/app/repositories/diagnostics_repository.py`
- Test: `tests/integration/test_backend_diagnostics_repository.py`

**Interfaces:**
- Consumes: `rules.text_heavy_result.CLASSIFIER_NAME` (`"text_heavy_v1"`), `rules.text_heavy_result.TEXT_HEAVY` (`"text_heavy"`) — existing constants, no change.
- Produces: `DiagnosticsRepository.get_statistics()`'s returned row gains three new integer attributes: `.ocr_missing_text_heavy_classification`, `.text_heavy_missing_embeddings`, `.embeddings_missing_lemmas`. Task 3 consumes these exact attribute names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_backend_diagnostics_repository.py`:

```python
async def test_get_statistics_counts_ocr_missing_text_heavy_classification(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    classified = await _new_image(db_session)
    unclassified = await _new_image(db_session)
    db_session.add_all([
        OCRText(image_id=classified.id, text="hello", confidence=0.9),
        ImageClassification(image_id=classified.id, classifier=CLASSIFIER_NAME, result=NOT_TEXT_HEAVY, details={}),
        OCRText(image_id=unclassified.id, text="hello", confidence=0.9),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()

    # classified image (despite having OCR text) must not count; only OCR-without-classification does
    assert after.ocr_missing_text_heavy_classification == before.ocr_missing_text_heavy_classification + 1


async def test_get_statistics_counts_text_heavy_missing_embeddings(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    text_heavy_no_embedding = await _new_image(db_session)
    text_heavy_with_embedding = await _new_image(db_session)
    db_session.add_all([
        ImageClassification(image_id=text_heavy_no_embedding.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}),
        ImageClassification(image_id=text_heavy_with_embedding.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}),
        OCRTextEmbedding(image_id=text_heavy_with_embedding.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    assert after.text_heavy_missing_embeddings == before.text_heavy_missing_embeddings + 1


async def test_get_statistics_counts_embeddings_missing_lemmas(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    embedded_no_lemmas = await _new_image(db_session)
    embedded_with_lemmas = await _new_image(db_session)
    db_session.add_all([
        OCRTextEmbedding(image_id=embedded_no_lemmas.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        OCRTextEmbedding(image_id=embedded_with_lemmas.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        OCRLemma(image_id=embedded_with_lemmas.id, lemma="hello"),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    assert after.embeddings_missing_lemmas == before.embeddings_missing_lemmas + 1


async def test_get_statistics_coverage_gap_counts_exclude_pending_images(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    pending = Image(filename=f"{uuid.uuid4()}.jpg", status="pending")
    db_session.add(pending)
    await db_session.flush()
    db_session.add_all([
        OCRText(image_id=pending.id, text="hello", confidence=0.9),
        OCRTextEmbedding(image_id=pending.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    # a pending image with OCR-but-no-classification and embeddings-but-no-lemmas must not
    # inflate either count -- scope is status == "active" only
    assert after.ocr_missing_text_heavy_classification == before.ocr_missing_text_heavy_classification
    assert after.embeddings_missing_lemmas == before.embeddings_missing_lemmas


async def test_get_statistics_fully_covered_image_appears_in_no_gap_count(db_session):
    before = await DiagnosticsRepository(db_session).get_statistics()

    image = await _new_image(db_session)
    db_session.add_all([
        OCRText(image_id=image.id, text="hello", confidence=0.9),
        ImageClassification(image_id=image.id, classifier=CLASSIFIER_NAME, result=TEXT_HEAVY, details={}),
        OCRTextEmbedding(image_id=image.id, embedding=[0.0] * OCR_TEXT_EMBEDDING_DIM),
        OCRLemma(image_id=image.id, lemma="hello"),
    ])
    await db_session.flush()

    after = await DiagnosticsRepository(db_session).get_statistics()
    assert after.ocr_missing_text_heavy_classification == before.ocr_missing_text_heavy_classification
    assert after.text_heavy_missing_embeddings == before.text_heavy_missing_embeddings
    assert after.embeddings_missing_lemmas == before.embeddings_missing_lemmas


async def _new_image(session) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    return image
```

Add these imports to the top of the test file, alongside its existing `from Storage.models import Embedding, Image, ImageExtras, ImageTag, OCRText`:

```python
from rules.text_heavy_result import CLASSIFIER_NAME, NOT_TEXT_HEAVY, TEXT_HEAVY
from Storage.models import ImageClassification, OCR_TEXT_EMBEDDING_DIM, OCRLemma, OCRTextEmbedding
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from repo root, per CLAUDE.md's integration-test invocation):
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_diagnostics_repository.py -v
```
Expected: the five new tests FAIL with `AttributeError: 'Row' object has no attribute 'ocr_missing_text_heavy_classification'` (or equivalent for the other two names) — `get_statistics()` doesn't select these columns yet.

- [ ] **Step 3: Implement the three new columns**

In `Backend/app/repositories/diagnostics_repository.py`, add `ImageClassification`, `OCRLemma`, `OCRTextEmbedding` to the existing `Storage.models` import, and add `from rules.text_heavy_result import CLASSIFIER_NAME, TEXT_HEAVY` as a new import line. Then add three more scalar-subquery columns to the `select(...)` call inside `get_statistics()`, right after the existing `duplicate_clusters` column:

```python
                select(func.count(TmpImageClusters.cluster_id.distinct()))
                    .scalar_subquery().label("duplicate_clusters"),
                select(func.count()).select_from(Image)
                    .where(
                        Image.status == "active",
                        exists(select(OCRText.image_id).where(OCRText.image_id == Image.id)),
                        ~exists(
                            select(ImageClassification.image_id)
                            .where(
                                ImageClassification.image_id == Image.id,
                                ImageClassification.classifier == CLASSIFIER_NAME,
                            )
                        ),
                    )
                    .scalar_subquery().label("ocr_missing_text_heavy_classification"),
                select(func.count()).select_from(Image)
                    .where(
                        Image.status == "active",
                        exists(
                            select(ImageClassification.image_id)
                            .where(
                                ImageClassification.image_id == Image.id,
                                ImageClassification.classifier == CLASSIFIER_NAME,
                                ImageClassification.result == TEXT_HEAVY,
                            )
                        ),
                        ~exists(select(OCRTextEmbedding.image_id).where(OCRTextEmbedding.image_id == Image.id)),
                    )
                    .scalar_subquery().label("text_heavy_missing_embeddings"),
                select(func.count()).select_from(Image)
                    .where(
                        Image.status == "active",
                        exists(select(OCRTextEmbedding.image_id).where(OCRTextEmbedding.image_id == Image.id)),
                        ~exists(select(OCRLemma.image_id).where(OCRLemma.image_id == Image.id)),
                    )
                    .scalar_subquery().label("embeddings_missing_lemmas"),
```

(Leave the existing `duplicate_clusters` column exactly as it is — the snippet above repeats its closing line only so you can see the insertion point; don't duplicate it.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_diagnostics_repository.py -v
```
Expected: all tests PASS, including the pre-existing ones (`test_get_statistics_counts_seeded_rows`, etc.) — this file shares one test database, so run the whole file, not just the five new tests.

- [ ] **Step 5: Run the full integration root**

Per CLAUDE.md's "Running the right test scope" gotcha, this touches shared repository code read by many tests — but `DiagnosticsRepository` itself is narrowly scoped (only this one file reads it), so the risk this gotcha warns about (a *shared* normalization/matching module breaking unrelated tests) doesn't apply here. Still, run the full root once as a sanity check before moving on:
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all tests PASS (or only pre-existing, unrelated failures — confirm against a clean `main` checkout if anything unexpected fails).

- [ ] **Step 6: Commit**

```bash
git add Backend/app/repositories/diagnostics_repository.py tests/integration/test_backend_diagnostics_repository.py
git commit -m "feat: add coverage-gap counts to DiagnosticsRepository.get_statistics()"
```

---

### Task 3: API — hand-written response model + endpoint + docs

**Files:**
- Modify: `Backend/app/api/diagnostics.py`
- Modify: `backend_api.md`
- Test: `Backend/tests/test_diagnostics_endpoints.py`

**Interfaces:**
- Consumes: `DiagnosticsRepository.get_statistics()`'s row attributes from Task 2 (`.ocr_missing_text_heavy_classification`, `.text_heavy_missing_embeddings`, `.embeddings_missing_lemmas`).
- Produces: `GET /api/diagnostics/statistics`'s JSON response gains `memes.ocr_missing_text_heavy_classification`, `memes.text_heavy_missing_embeddings`, `memes.embeddings_missing_lemmas` (all integers). Task 4 (frontend) consumes these three field names.

- [ ] **Step 1: Write the failing tests**

In `Backend/tests/test_diagnostics_endpoints.py`, add the three new fields to `_fake_stats_row`'s `defaults` dict:

```python
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
```

Add a new test method to `TestStatistics`:

```python
    def test_statistics_includes_coverage_gap_counts(self, client, mock_diagnostics_repo):
        mock_diagnostics_repo.get_statistics.return_value = _fake_stats_row(
            ocr_missing_text_heavy_classification=7, text_heavy_missing_embeddings=4, embeddings_missing_lemmas=2,
        )

        response = client.get("/api/diagnostics/statistics")

        assert response.status_code == 200
        data = response.json()
        assert data["memes"]["ocr_missing_text_heavy_classification"] == 7
        assert data["memes"]["text_heavy_missing_embeddings"] == 4
        assert data["memes"]["embeddings_missing_lemmas"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd Backend && pytest tests/test_diagnostics_endpoints.py -v
```
Expected: `test_statistics_includes_coverage_gap_counts` FAILS with a `KeyError: 'ocr_missing_text_heavy_classification'` (the hand-written `MemeStats` doesn't declare the field, so Pydantic strips it from the response — this is the exact gotcha the Global Constraints section calls out).

- [ ] **Step 3: Implement — update the hand-written `MemeStats` and the endpoint**

In `Backend/app/api/diagnostics.py`, add three fields to `MemeStats`:

```python
class MemeStats(BaseModel):
    total: int
    pending: int
    rejected: int
    with_embeddings: int
    with_ocr: int
    with_tags: int
    without_tags: int
    with_descriptions: int
    with_concept_tags: int
    flagged: int
    duplicate_clusters: int
    ocr_missing_text_heavy_classification: int
    text_heavy_missing_embeddings: int
    embeddings_missing_lemmas: int
```

And pass the three new values through in `statistics()`'s `MemeStats(...)` construction:

```python
        memes=MemeStats(
            total=row.total_memes,
            pending=row.pending,
            rejected=row.rejected,
            with_embeddings=row.with_embeddings,
            with_ocr=row.with_ocr,
            with_tags=row.with_tags,
            without_tags=row.without_tags,
            with_descriptions=row.with_descriptions,
            with_concept_tags=row.with_concept_tags,
            flagged=row.flagged,
            duplicate_clusters=row.duplicate_clusters,
            ocr_missing_text_heavy_classification=row.ocr_missing_text_heavy_classification,
            text_heavy_missing_embeddings=row.text_heavy_missing_embeddings,
            embeddings_missing_lemmas=row.embeddings_missing_lemmas,
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd Backend && pytest tests/test_diagnostics_endpoints.py -v
```
Expected: all tests PASS, including the pre-existing ones in `TestStatistics`.

- [ ] **Step 5: Update `backend_api.md`**

In `backend_api.md`, find the `### StatisticsResponse` section's example JSON block (documents the
`memes` object) and add three lines to the `"memes"` object, right after `"duplicate_clusters": "number"`:

```json
    "duplicate_clusters": "number",
    "ocr_missing_text_heavy_classification": "number",
    "text_heavy_missing_embeddings": "number",
    "embeddings_missing_lemmas": "number"
```

(This replaces the line `"duplicate_clusters": "number"` — note the comma moves from the last line
to the new second-to-last line. Keep the rest of the block — the closing `},` for `memes`, and the
`content`/`trends` objects that follow — unchanged.)

- [ ] **Step 6: Manual smoke-test against a live server (per CLAUDE.md's "Before committing backend changes")**

Ports 8081/8082/8083 are permanently occupied by the developer's live `metal`/`general`/`it`
environments (CLAUDE.md, `environments/Environments.md`) — never bind a server to them for testing.
Instead, start a temporary backend on a verified-free port outside that table, e.g. 8189
(`netstat -ano | findstr :8189` confirmed free before use), per CLAUDE.md's uvicorn command with
`--port 8189` in place of one of the reserved ports, then:
```bash
curl -s http://localhost:8189/api/diagnostics/statistics | python -m json.tool
```
Expected: the response's `memes` object includes `ocr_missing_text_heavy_classification`, `text_heavy_missing_embeddings`, `embeddings_missing_lemmas` with real integer values — this confirms the hand-written model actually serializes them in a live response, not just in the mocked test. Also hit `/api/diagnostics/health` and `/api/images?limit=1` per CLAUDE.md's smoke-test minimum.

- [ ] **Step 7: Commit**

```bash
git add Backend/app/api/diagnostics.py Backend/tests/test_diagnostics_endpoints.py backend_api.md
git commit -m "feat: surface coverage-gap counts on GET /api/diagnostics/statistics"
```

---

### Task 4: Frontend — Statistics page

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/StatisticsPage.tsx`

**Interfaces:**
- Consumes: `StatisticsResponse.memes.ocr_missing_text_heavy_classification`, `.text_heavy_missing_embeddings`, `.embeddings_missing_lemmas` (Task 1's regenerated type, Task 3's live data).

- [ ] **Step 1: Add three cells to the "Pipeline coverage" section**

In `StatisticsPage.tsx`, extend the existing "Pipeline coverage" section's `StatGrid` cells array:

```tsx
      <section>
        <h2 className="text-lg font-semibold mb-3">Pipeline coverage</h2>
        <StatGrid cells={[
          { label: "With OCR", value: `${n(memes.with_ocr)} (${pct(memes.with_ocr, memes.total)})` },
          { label: "With embeddings", value: `${n(memes.with_embeddings)} (${pct(memes.with_embeddings, memes.total)})` },
          { label: "With tags", value: `${n(memes.with_tags)} (${pct(memes.with_tags, memes.total)})` },
          { label: "With descriptions", value: `${n(memes.with_descriptions)} (${pct(memes.with_descriptions, memes.total)})` },
          { label: "Without descriptions", value: `${n(withoutDescriptions)} (${pct(withoutDescriptions, memes.total)})` },
          { label: "With concept assignments", value: `${n(memes.with_concept_tags)} (${pct(memes.with_concept_tags, memes.total)})` },
          { label: "OCR without text-heavy classification", value: n(memes.ocr_missing_text_heavy_classification) },
          { label: "Text-heavy without OCR-text embeddings", value: n(memes.text_heavy_missing_embeddings) },
          { label: "Embeddings without OCR lemmas", value: n(memes.embeddings_missing_lemmas) },
        ]} />
      </section>
```

(These three use `n(...)` alone, no `pct(...)` — they're gap counts with no fixed denominator, not coverage-of-total like the section's other cells, matching the spec's §4 design.)

- [ ] **Step 2: Type-check and lint**

Run (from `Frontend/memes-frontend/`):
```bash
tsc -b
eslint src/
```
Expected: both PASS with zero errors/warnings — `memes.ocr_missing_text_heavy_classification` etc. type-check against Task 1's regenerated `StatisticsResponse` type.

- [ ] **Step 3: Run the frontend test suite**

Run (from `Frontend/memes-frontend/`):
```bash
vitest run
```
Expected: PASS — `StatisticsPage.tsx` has no existing test file (confirmed during spec review), so this is a regression check on the rest of the suite, not new coverage. No new test file is added in this task, matching the page's current test coverage level (per the spec's Non-goals).

- [ ] **Step 4: Commit**

```bash
git add Frontend/memes-frontend/src/pages/StatisticsPage.tsx
git commit -m "feat: render coverage-gap counts on the Statistics page"
```

---

### Task 5: Rollout verification (controller-only, read-only)

**Files:** none modified — this task is verification only.

**Interfaces:** none produced — terminal task.

- [ ] **Step 1: Confirm each environment's backend still starts cleanly**

Per CLAUDE.md's "Before committing backend changes," for each of the three environments already running on the developer's workstation (ports 8081/8082/8083 — do not start a new one on those ports; if they need a restart to pick up the code change, that's the developer's call, not something to force), confirm `/api/diagnostics/health` returns `{"backend": true, "database": true}` and `/api/diagnostics/statistics` returns the three new fields with real integer values.

- [ ] **Step 2: Read-only sanity check against real data**

Using `DATABASE_URL_READONLY` from each environment's `environments/.env.<environment>` file — **run directly by the controller, never delegated to a subagent, and explicitly labeled read-only in any dispatch prompt if a subagent is ever involved in *reading* the output** (per CLAUDE.md's live-database-access rule) — compare each environment's `embeddings_missing_lemmas` count against what's already known from the corroboration-gate rollout:

```sql
SELECT count(*) FROM ocr_text_embeddings e
JOIN images i ON i.id = e.image_id AND i.status = 'active'
WHERE NOT EXISTS (SELECT 1 FROM ocr_lemmas l WHERE l.image_id = e.image_id);
```

Expected: `metal` and `it` read at or near zero (per `docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md`'s rollout, both fully caught up). `general` may read non-zero — that's expected and confirms the count measures the right thing (per the spec's Rollout section), not a bug to fix in this plan.

- [ ] **Step 3: Update the spec with a Rollout Outcome section**

Append to `docs/superpowers/specs/2026-09-23-duplicate-matching-coverage-gap-diagnostics.md`:

```markdown
## Rollout outcome (<date>)

| Environment | `ocr_missing_text_heavy_classification` | `text_heavy_missing_embeddings` | `embeddings_missing_lemmas` |
|---|---|---|---|
| metal   | <count> | <count> | <count> |
| general | <count> | <count> | <count> |
| it      | <count> | <count> | <count> |

<any notable finding from Step 2's spot-check>
```

Change the spec's status line from `status: approved` to `status: done`.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-23-duplicate-matching-coverage-gap-diagnostics.md
git commit -m "docs: mark coverage-gap-diagnostics spec done with rollout outcome"
```
