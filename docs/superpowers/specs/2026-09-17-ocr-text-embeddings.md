# OCR Text Embeddings — Compute and Store

status: approved
Originates from: the 2026-09-16/17 conversation continuing the text-heavy-classifier work
(`docs/superpowers/specs/2026-09-15-text-heavy-classifier.md`) — that spec's Non-goals named
"OCR-text-embedding comparison for the text_heavy bucket vs. today's CLIP-only comparison" as
explicit future work. This spec is the first of two: compute-and-store the new signal here;
a follow-up spec (not yet written) wires it into actual duplicate-matching, clustering, and
review-UI sort order once this data exists to calibrate against — mirroring exactly how the
text-heavy classifier itself was split (compute+store first, informed by real validation,
before any routing logic changed).

## Problem

CLIP visual similarity is the only signal `tmp_duplicates`/`clusterize.py`/ingestion Tier A/B
review use to find candidate duplicate pairs today. This is structurally blind to one entire
class of duplicate: two `text_heavy` images (chat/tweet/dialog screenshots) that share the same
or near-identical text content but look nothing alike visually — different app chrome, different
crop, different theme/colors, a re-typed repost, etc. CLIP was never going to catch these; it's
a visual-similarity model being asked a text-similarity question.

A quick sanity check (not full calibration — see Non-goals) against the live `paraphrase-
multilingual-MiniLM-L12-v2` model already wired into `ai/sbert.py` (its own default, separate
from `build_description_note_embeddings.py`'s English-tuned override) confirms the underlying
approach is sound on this corpus's actual language mix. Three real-shaped OCR text samples
(two near-duplicate Russian tweet screenshots differing only in phrasing, one unrelated Russian
tweet, one unrelated English tweet), embedded and compared via cosine distance:

- Near-duplicate Russian pair: **0.086**
- Unrelated Russian pair: **0.490**
- Cross-language unrelated pair: **0.712**

Clean separation, in the direction expected, on genuinely multilingual content — enough to
justify building the storage layer and gathering real corpus-scale data, not enough to lock any
threshold (three hand-picked sentences is not a corpus).

## Goal

For every image currently classified `text_heavy` (`image_classifications.classifier =
'text_heavy_v1'`, `result = 'text_heavy'`), compute a sentence embedding of that image's OCR
text — the same confidence/language-filtered, deduped, concatenated text a reviewer already sees
in the ingestion review UI, not raw unfiltered OCR blocks — and store it durably, keyed to the
image. This produces the dataset the follow-up spec needs to empirically calibrate real
similarity thresholds, the same way the classifier's own thresholds were derived from live
validation against real data rather than guessed upfront.

## Non-goals

- **Wiring this into `tmp_duplicates`, `ingest_find_duplicates.py`, `rebuild_duplicates.py`,
  `clusterize.py`, `Backend/app/services/ingestion_service.py`, or any review-UI sort order.**
  This spec computes and stores the embedding only. The follow-up spec (informed by real
  distance data this spec's rollout produces) designs the actual matching mechanics: a new
  `distance_source` column on `tmp_duplicates`, a text-embedding probe scoped to text-heavy-vs-
  text-heavy pairs with its own tight/loose thresholds mirroring today's `PROXIMITY_THRESHOLD`/
  `DUPLICATES.THRESHOLD` shape, a tight CLIP safety-net probe for near-exact visual duplicates,
  `clusterize.py` union-find accepting `ocr_text`-sourced edges at their own threshold, and
  `distance_source`-aware sorting (grouped before raw distance, never comparing the two scales
  directly) in both ingestion review and the corpus-wide admin Duplicates page.
- **Threshold calibration.** No tight/loose cutoff numbers are decided by this spec — that's
  explicitly the follow-up spec's job, using this spec's real stored data.
- **Embedding the whole corpus.** Only images currently classified `text_heavy` are embedded —
  this signal is only ever used for text-heavy-vs-text-heavy pairs (per the approved design), so
  embedding the other ~85-98% of the corpus (see the classifier spec's rollout numbers) would be
  pure waste.
- **Re-embedding tooling / staleness tracking.** Unlike `DescriptionNoteEmbedding` (which tracks
  `embedding_built_at` vs. the note's `updated_at`, since a human can edit a note after the fact),
  OCR text for an image is effectively immutable once OCR has run — the only thing that changes
  it is a full corpus-wide OCR re-run, an already-documented rare, explicit, manual operation
  (CLAUDE.md's "Full re-run" section). This script is idempotent the same way
  `classify_text_heavy.py` is: an image already present in `ocr_text_embeddings` is simply
  excluded from future runs, no staleness predicate. If OCR text or the classification changes
  later, re-embedding is a manual concern, not automated here — matches the classifier spec's own
  "no re-classification tooling" non-goal.
- **A fourth signal, or anything about the classifier's own known false-positive class.** Out of
  scope for this spec entirely; unrelated to embedding computation.

## Design

### §1. Schema

`Storage/models.py` — new constant and model, same shape as `DescriptionNoteEmbedding` but keyed
directly to `images.id` (no intermediate note-ownership table) and dimensioned for MiniLM, not
bge-large:

```python
OCR_TEXT_EMBEDDING_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 -- verified empirically
                               # (ai/sbert.py's SbertModel().embed_text(...).shape == (384,)),
                               # not bge-large-en-v1.5's 1024 (TEXT_EMBEDDING_DIM).


class OCRTextEmbedding(Base):
    __tablename__ = "ocr_text_embeddings"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(Vector(OCR_TEXT_EMBEDDING_DIM))
    computed_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="ocr_text_embedding")

    __table_args__ = (
        Index(
            "ix_ocr_text_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
```

Add `Image.ocr_text_embedding = relationship("OCRTextEmbedding", back_populates="image",
uselist=False, cascade="all, delete-orphan")` to `Image`'s relationship block, next to
`classifications`.

Migration: plain `alembic revision --autogenerate`, same shape as
`6f770efe4fb4_add_image_classifications_table.py` — brand-new empty table, no `CONCURRENTLY`
concern, no existing-data risk.

### §2. Shared OCR-text-concatenation logic (small, behavior-preserving refactor)

`Backend/app/repositories/ingestion_repository.py`'s `get_ocr_texts()` already implements exactly
the filtering/dedup/ordering this spec needs (confidence/lang-score threshold, most-language-
plausible-first ordering, identical-block dedup) — it's what the review UI already shows. Rather
than duplicate that logic in a batch script (a second copy that could silently drift, exactly the
class of shared-code risk CLAUDE.md's own "running the right test scope" gotcha warns about),
extract the row-processing body (everything after the `SELECT` and `.all()`) into a pure,
side-effect-free function in `repository/ocr_text.py`:

```python
def concatenate_ocr_rows(rows, confidence_min: float, lang_score_min: float) -> dict:
    """rows: iterable of (image_id, text, confidence, lang_score) tuples -- e.g. straight from
    a SELECT OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score. Drops
    blocks below either threshold, orders survivors most-language-plausible first (Russian
    leads instead of the EN/ES EasyOCR readers' Latin transliteration noise), dedupes identical
    block text per image, and concatenates into one string per image_id. Images with no
    surviving block are simply absent from the result."""
    def _order_key(row):
        _, _, confidence, lang_score = row
        return (
            lang_score is None,
            -(lang_score if lang_score is not None else 0.0),
            confidence is None,
            -(confidence if confidence is not None else 0.0),
        )

    rows = sorted(rows, key=_order_key)
    by_image: dict = {}
    seen: dict = {}
    for image_id, text, confidence, lang_score in rows:
        if confidence is not None and confidence < confidence_min:
            continue
        if lang_score is not None and lang_score < lang_score_min:
            continue
        block = (text or "").strip()
        if not block:
            continue
        seen_for_image = seen.setdefault(image_id, set())
        if block in seen_for_image:
            continue
        seen_for_image.add(block)
        by_image.setdefault(image_id, []).append(block)
    return {image_id: " ".join(parts) for image_id, parts in by_image.items()}
```

`IngestionRepository.get_ocr_texts()` keeps its own `SELECT` exactly as-is; only the body after
`result = await self.session.execute(...)` changes, replacing the sort/filter/dedup/concat block
with a single call to the relocated function:

```python
    async def get_ocr_texts(
        self, image_ids, confidence_min: float, lang_score_min: float
    ) -> dict:
        """Concatenated OCR text per image id for the review UI. See
        repository/ocr_text.py's concatenate_ocr_rows for the filtering/ordering/dedup
        rules this delegates to."""
        if not image_ids:
            return {}
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(image_ids))
        )
        return concatenate_ocr_rows(result.all(), confidence_min, lang_score_min)
```

Add `from repository.ocr_text import concatenate_ocr_rows` to
`Backend/app/repositories/ingestion_repository.py`'s imports; the `_order_key` closure and the
`sorted`/`by_image`/`seen` block it replaces are deleted from this file entirely (they now live
only in `concatenate_ocr_rows`). Same inputs produce the same outputs, since it's the same code
relocated — fully covered by `Backend/tests/test_ingestion_repository.py`'s existing
`TestGetOcrTexts` suite, which continues to pass unchanged and serves as the regression safety
net for the move.

### §3. Repository

New file `repository/ocr_text_embeddings.py`, mirroring `repository/description_note_embeddings.py`'s
shape (own file, own class, per this codebase's one-embedding-table-per-repository-file
convention) but simpler per the Non-goals above (no staleness tracking):

```python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from repository.ocr_text import concatenate_ocr_rows
from Storage.models import Image, ImageClassification, OCRText, OCRTextEmbedding


class OCRTextEmbeddingsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_text_heavy_images_needing_embedding(
        self, classifier: str, confidence_min: float, lang_score_min: float, status: str = "active",
    ) -> dict:
        """Concatenated OCR text (see concatenate_ocr_rows) per text_heavy image not yet in
        ocr_text_embeddings. Images whose filtered text comes out empty (possible: the
        classifier's own bbox-coverage filter only checks confidence, not lang_score, so a
        text_heavy image's OCR could in principle be entirely low-lang-score noise) are simply
        absent from the result -- unlike classify_text_heavy.py's "unreadable" outcome, this
        has no separate counter; the caller's to-embed count is just len(this dict)."""
        already_embedded = select(OCRTextEmbedding.image_id).scalar_subquery()
        text_heavy_ids = (
            select(ImageClassification.image_id)
            .where(ImageClassification.classifier == classifier, ImageClassification.result == "text_heavy")
            .scalar_subquery()
        )
        candidate_ids = (
            select(Image.id)
            .where(
                Image.status == status,
                Image.id.in_(text_heavy_ids),
                Image.id.not_in(already_embedded),
            )
        )
        candidates = (await self.session.execute(candidate_ids)).scalars().all()
        if not candidates:
            return {}

        rows = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(candidates))
        )
        texts = concatenate_ocr_rows(rows.all(), confidence_min, lang_score_min)
        return {image_id: texts[image_id] for image_id in candidates if image_id in texts}

    async def save(self, image_id, embedding: list[float]) -> None:
        stmt = (
            insert(OCRTextEmbedding)
            .values(image_id=image_id, embedding=embedding)
            .on_conflict_do_update(
                index_elements=["image_id"],
                set_={"embedding": embedding},
            )
        )
        await self.session.execute(stmt)
```

`get_text_heavy_images_needing_embedding` returns a `dict[image_id, text]` (not a list of rows)
since the batch script needs exactly that shape to iterate and embed — matches the return shape
`IngestionRepository.get_ocr_texts()` already establishes for the same kind of "OCR text per
image" result.

### §4. Batch script

New file `batch/build_ocr_text_embeddings.py`, mirroring `build_description_note_embeddings.py`'s
`run()`/`main()` shape and periodic-commit pattern exactly (that script already commits every
`settings.GENERAL.BATCH_SIZE` images, so no repeat of the all-or-nothing-run risk the
text-heavy-classifier's own final review caught):

```python
"""Computes sentence embeddings for the OCR text of every image classified text_heavy (see
docs/superpowers/specs/2026-09-15-text-heavy-classifier.md), storing them in
ocr_text_embeddings for a future duplicate-matching signal (see
docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md). Safe to re-run: only images not yet
in ocr_text_embeddings are processed."""
import argparse
import asyncio
import uuid

from ai.sbert import SbertModel
from batch.run_tracking import finish_existing_run, tracked_run
from batch.utils.progress import ProgressTracker
from config.settings import load_env, settings
from metrics.listener import SimpleMetricsListener
from repository.ocr_text_embeddings import OCRTextEmbeddingsRepository
from Storage.db import AsyncSessionLocal

CLASSIFIER_NAME = "text_heavy_v1"
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"  # ai/sbert.py's own default --
                                                             # named explicitly here so the
                                                             # model in use is visible at a
                                                             # glance, defensive against that
                                                             # default ever changing later.


async def run(session, confidence_min: float, lang_score_min: float, status: str) -> SimpleMetricsListener:
    repo = OCRTextEmbeddingsRepository(session)
    texts = await repo.get_text_heavy_images_needing_embedding(
        CLASSIFIER_NAME, confidence_min, lang_score_min, status=status)
    print(f"Images to embed: {len(texts)}")

    embedder = SbertModel(model_name=EMBEDDING_MODEL)
    metrics = SimpleMetricsListener()
    tracker = ProgressTracker(total=len(texts), report_every=settings.GENERAL.PROGRESS_EVERY)

    for i, (image_id, text) in enumerate(texts.items()):
        vector = embedder.embed_text(text)
        await repo.save(image_id, vector.tolist())
        metrics.increment("embedded")
        tracker.mark_done()
        if (i + 1) % settings.GENERAL.BATCH_SIZE == 0:
            await session.commit()

    await session.commit()
    tracker.summary()
    return metrics


async def main(status: str = "active", trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    confidence_min = settings.OCR.CONFIDENCE_MIN
    lang_score_min = settings.OCR.LANG_SCORE_MIN

    if run_id is not None:
        async with finish_existing_run(run_id):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, confidence_min, lang_score_min, status)
    else:
        async with tracked_run(kind="build_ocr_text_embeddings", trigger=trigger):
            async with AsyncSessionLocal() as session:
                metrics = await run(session, confidence_min, lang_score_min, status)

    print("Embedding results:")
    metrics.print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--status", choices=["active", "pending"], default="active")
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main(status=args.status))
```

Note the `texts` dict from `get_text_heavy_images_needing_embedding` may have fewer entries than
the number of candidate text_heavy images found (any image whose filtered OCR text came out
empty is simply absent) — `len(texts)` printed up front is therefore already the true
to-be-embedded count, not an over-count that silently short-circuits partway through like
`classify_text_heavy.py`'s `unreadable` counter does. No separate "unembeddable" metric is needed
for that case; it's just not in the dict to begin with.

### §5. Admin-batch registry + CLAUDE.md entry

`environments/batch_registry.yaml` gains an entry (this is the exact step the text-heavy
classifier's own final review caught missing after the fact — included here from the start, not
discovered later):

```yaml
build_ocr_text_embeddings:
  module: batch.build_ocr_text_embeddings
  kind: build_ocr_text_embeddings
```

CLAUDE.md gains an entry alongside `classify_text_heavy` in the `# Maintenance (run as needed)`
block:

```
build_ocr_text_embeddings   → computes sentence embeddings (paraphrase-multilingual-MiniLM-
                               L12-v2, 384-dim) for the OCR text of every text_heavy-classified
                               image, storing them in ocr_text_embeddings. Compute+store only --
                               no matching/routing logic reads this table yet. Admin-triggerable
                               from /admin/batches, manual-trigger only, not scheduled. --status
                               defaults to active; --status pending covers an in-flight
                               ingestion batch. See
                               docs/superpowers/specs/2026-09-17-ocr-text-embeddings.md.
```

## Testing

Global `repository/` classes in this codebase are tested via real-DB integration tests, not
mocked-session unit tests — `repository/description_note_embeddings.py` /
`tests/integration/test_description_note_embeddings_repository.py` is the exact precedent this
spec follows; that convention differs from `Backend/app/repositories/`'s mocked-session style
(e.g. `Backend/tests/test_ingestion_repository.py`), which is a separate, Backend-specific layer.

- **Repository** (`Backend/tests/test_ingestion_repository.py`, mocked session, no DB, existing
  suite): confirm `get_ocr_texts()` still produces identical output after §2's refactor delegates
  its row-processing body to `concatenate_ocr_rows` — the existing `TestGetOcrTexts` cases already
  are this test (same inputs, same outputs, since it's the same code relocated); they must
  continue to pass unchanged as the regression safety net for the move. No new Backend tests
  needed — this refactor changes no observable behavior.
- **Repository** (new, `tests/integration/test_ocr_text_embeddings_repository.py`, real DB):
  `get_text_heavy_images_needing_embedding` — empty when no text_heavy images exist; excludes
  already-embedded images; excludes a non-text_heavy image even with OCR text present; excludes an
  image whose OCR text is present but entirely filtered out (all low confidence/lang_score) —
  this is also where `concatenate_ocr_rows`'s behavior gets real end-to-end coverage in its new
  call site, on top of the existing Backend-side coverage of the function itself; `save()`'s
  upsert shape (a second `save()` call for the same image_id overwrites, not duplicates).
- **Integration** (`tests/integration/test_build_ocr_text_embeddings.py`, real DB): full `run()`
  — a text_heavy image with real OCR rows gets a real 384-dim vector persisted; a re-run finds
  zero candidates for an already-embedded image; a non-text_heavy image is excluded even with OCR
  text present; an image whose OCR text is present but entirely filtered out is excluded and not
  counted as embedded.

## Rollout

1. Ship the migration + code. No behavior change to any existing path — new table, new
   independent manually-triggered batch script, and one mechanical, test-covered refactor of an
   existing method's internals (not its observable behavior).
2. Apply the migration to all three live environments (new, empty table — no `CONCURRENTLY`
   concern, no live-data risk, but still a live-database write requiring the same controller-only,
   explicit-go-ahead handling as every prior live migration this session).
3. With explicit go-ahead, run `build_ocr_text_embeddings.py --status active` against each
   environment to embed the existing text_heavy corpus (406 / 4,084 / 178 images per the
   classifier's own rollout counts for metal/general/it), then `--status pending` for any
   in-flight ingestion batch.
4. Verify via a read-only count (`SELECT count(*) FROM ocr_text_embeddings`) that it roughly
   matches each environment's `text_heavy` count from the classifier's rollout (allowing for the
   empty-after-filtering exclusion). Then, still read-only, pull a handful of **real** stored
   embedding pairs per environment and inspect their cosine distances directly (`embedding1 <=>
   embedding2` via `DATABASE_URL_READONLY`) for a few deliberately-chosen pairs: two images that
   look like genuine template reposts by eye, and two that look unrelated. This is the same
   spot-check spirit as the classifier's own rollout verification, extended to real corpus data
   instead of this spec's three hand-picked sentences — it's meant to build confidence the signal
   behaves sensibly at real scale, **not** to lock a threshold (that's the follow-up spec's job,
   with a larger, more systematic sample).
5. Mark this spec `done` with a "Rollout outcome" section (embedded-image counts per environment,
   notable spot-check findings). Commit, delete the SDD workspace, merge per
   `finishing-a-development-branch`.
6. Write the follow-up spec ("wire text-embedding matching into Tier A/B and corpus-wide dedup")
   using this rollout's real data to ground its threshold proposals, per the approved design from
   the brainstorming conversation this spec originates from.
