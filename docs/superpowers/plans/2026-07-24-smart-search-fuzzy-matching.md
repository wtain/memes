# Smart Search Fuzzy Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add trigram-similarity fuzzy-match fallback to search: when a query lemma has no exact match, retry with Postgres `pg_trgm` similarity, guarded by a minimum lemma length to avoid the short-word false-positive risk already documented in this codebase.

**Architecture:** A new GIN trigram index on `ocr_lemmas.lemma` (via `pg_trgm`, enabled per-database). `repository/ocr_lemmas.py`'s `matching_image_ids` gains a per-lemma exact-then-fuzzy fallback, composing unchanged with its existing AND-across-lemmas logic. Two new settings (`search.fuzzy_min_lemma_length`, `search.fuzzy_similarity_threshold`) carry the empirically-derived thresholds from the design doc.

**Tech Stack:** Python 3.11, SQLAlchemy async ORM, PostgreSQL with `pg_trgm`, pytest/pytest-asyncio, Alembic.

## Global Constraints

- Thresholds are fixed, empirically-derived values from the design doc — do not adjust them without re-running the same kind of empirical validation (real `similarity()` queries against real data) that produced them: `search.fuzzy_min_lemma_length = 5`, `search.fuzzy_similarity_threshold = 0.35`.
- No new Backend dependency — this feature stays entirely in SQL (`pg_trgm`), consistent with the design's explicit reasoning for avoiding a Python-side (`rapidfuzz`) approach at query time.
- `Backend/tests/`, `tests/integration/`, `batch/tests/`, `tests/rules/` are separate `pytest.ini` roots — never combine them in one `pytest` invocation.
- `tests/integration/` requires `DATABASE_URL` set explicitly on the command line, e.g.:
  `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
- Spec: `docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md` — read it before starting if a task here is unclear on rationale, especially the empirical similarity-score table that justifies the threshold values.
- Note: `pg_trgm` was already manually enabled on the `metal` database during design investigation (a safe, additive, idempotent `CREATE EXTENSION IF NOT EXISTS` — confirmed harmless). Task 1's migration is still required as the real, repeatable, versioned path — running it against `metal` will just no-op the extension creation and add the new index (which doesn't exist yet).

---

### Task 1: Schema — enable `pg_trgm`, add the trigram index, add settings

**Files:**
- Modify: `Storage/models.py`
- Modify: `environments/settings.yaml`
- Create: `Storage/alembic/versions/<generated>_add_ocr_lemmas_trigram_index.py`

**Interfaces:**
- Produces: a GIN trigram index on `ocr_lemmas.lemma`; `settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH` (5) and `settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD` (0.35), readable via the existing Dynaconf `settings` object exactly like `settings.BOW.MIN_WORD_LENGTH` already is.

- [ ] **Step 1: Add the trigram index to the `OCRLemma` model**

In `Storage/models.py`, `OCRLemma`'s `__table_args__` currently reads:

```python
    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
    )
```

Change it to:

```python
    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
        Index(
            "ix_ocr_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
    )
```

This matches the exact syntax convention already used by `Embedding`'s HNSW vector index
(`ix_embeddings_embedding_hnsw_cosine`, same file) — a `postgresql_using`/`postgresql_ops`
pair on a plain `Index(...)` in `__table_args__`.

- [ ] **Step 2: Add the new settings**

In `environments/settings.yaml`, add a new top-level domain group (alongside `ocr`,
`bow`, etc.):

```yaml
search:
  fuzzy_min_lemma_length: 5
  fuzzy_similarity_threshold: 0.35
```

- [ ] **Step 3: Confirm the model imports cleanly and settings resolve**

Run: `python -c "from Storage.models import OCRLemma; print([i.name for i in OCRLemma.__table_args__])"`
Expected: `['ix_ocr_lemmas_lemma', 'ix_ocr_lemmas_lemma_trgm']`, no import errors.

Run: `python -c "from config.settings import settings; print(settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH, settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD)"`
Expected: `5 0.35`.

- [ ] **Step 4: Generate the Alembic migration**

From `Storage/`, with env vars loaded for the `metal` environment (schema is identical
across metal/general/it):

```powershell
Get-Content ..\environments\.env.metal | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic revision --autogenerate -m "add_ocr_lemmas_trigram_index"
```

Expected: a new file `Storage/alembic/versions/<hash>_add_ocr_lemmas_trigram_index.py`.
Autogenerate detects the new `Index(...)` from the model change (Step 1) and generates a
`create_index` call for it — but it will **not** generate the `CREATE EXTENSION`
statement, since that isn't an ORM-representable concept. You must add that by hand in
the next step.

- [ ] **Step 5: Add the extension creation, verify the generated migration**

Open the generated file. `upgrade()` should already contain something equivalent to:

```python
    op.create_index(
        'ix_ocr_lemmas_lemma_trgm', 'ocr_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'}
    )
```

Add the extension creation as the **first line** of `upgrade()` (must run before the
index creation, since the index's operator class depends on the extension existing):

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_index(
        'ix_ocr_lemmas_lemma_trgm', 'ocr_lemmas', ['lemma'],
        unique=False, postgresql_using='gin',
        postgresql_ops={'lemma': 'gin_trgm_ops'}
    )
```

And in `downgrade()`, drop the index (leave the extension in place — dropping a
Postgres extension other code might come to depend on later is not this migration's
call to make; `vector` is handled the same way, never dropped in any downgrade in this
codebase):

```python
def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ocr_lemmas_lemma_trgm', table_name='ocr_lemmas')
```

If autogenerate produced anything else unrelated in the same migration (drift from
some other pre-existing model/DB mismatch, similar to the `tmp_duplicates` drift
encountered in an earlier round), remove it — this migration must be scoped to only the
extension and the one new index.

- [ ] **Step 6: Apply the migration to the local metal dev DB**

```powershell
alembic upgrade head
```

Expected: no errors. Confirm directly:

```powershell
psql -h 127.0.0.1 -p 5432 -U ocr -d ocrdb -c "SELECT indexname FROM pg_indexes WHERE tablename = 'ocr_lemmas';"
```

Expected output includes both `ix_ocr_lemmas_lemma` and `ix_ocr_lemmas_lemma_trgm`.

- [ ] **Step 7: Commit**

```bash
git add Storage/models.py environments/settings.yaml Storage/alembic/versions/
git commit -m "feat: add pg_trgm trigram index on ocr_lemmas.lemma, fuzzy-match settings"
```

---

### Task 2: Query implementation — exact-first, fuzzy fallback

**Files:**
- Modify: `repository/ocr_lemmas.py`
- Modify: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Changes: `matching_image_ids`'s internal per-lemma matching now tries exact match
  first; only when that returns nothing, and the lemma length is
  `>= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH`, retries via
  `similarity(...) >= settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD`. External signature,
  `None`/empty-set-vs-non-empty-set semantics, and the AND-across-lemmas behavior are
  all unchanged.

**Depends on:** Task 1 (the trigram index and settings must exist for this to work; the
new tests in this task require `similarity()` to be callable, which needs `pg_trgm`
enabled — already true after Task 1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_ocr_lemmas_repository.py` (after the existing
`matching_image_ids` tests):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_exact_match_takes_precedence_over_fuzzy(db_session):
    exact = Image(filename=f"{uuid.uuid4()}.jpg")
    similar_only = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([exact, similar_only])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=exact.id, lemma="реклама"),
        OCRLemma(image_id=similar_only.id, lemma="рекламо"),
    ])
    await db_session.flush()

    ids = await matching_image_ids(db_session, "реклама")

    assert ids == {exact.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_fuzzy_fallback_finds_misspelled_lemma_when_no_exact_match(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="рекламо"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "реклама")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_short_lemma_below_length_guard_is_not_fuzzy_matched(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="код"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "кот")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_no_similar_match_returns_empty_set(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="совершенно"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "различие")

    assert ids == set()
```

- [ ] **Step 2: Run to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v -k "fuzzy or precedence"`
Expected: `test_exact_match_takes_precedence_over_fuzzy` passes already (no code change
needed for pure-exact-match behavior — this is fine, it's here as a regression guard);
`test_fuzzy_fallback_finds_misspelled_lemma_when_no_exact_match` FAILS (`assert set() ==
{image.id}` — no fuzzy fallback exists yet, so a misspelled-only lemma finds nothing);
`test_short_lemma_below_length_guard_is_not_fuzzy_matched` passes already (nothing to
guard against yet, coincidentally correct); `test_no_similar_match_returns_empty_set`
passes already. The one genuinely RED test is the fuzzy-fallback one — that's the
behavior this task adds.

- [ ] **Step 3: Implement the fuzzy fallback**

Replace `repository/ocr_lemmas.py`'s `matching_image_ids` function and add the two new
helper functions directly above it:

```python
async def _exact_lemma_ids(session: AsyncSession, lemma: str) -> set:
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())
    result = await session.execute(union(ocr_subq, tag_subq))
    return {row[0] for row in result.all()}


async def _fuzzy_lemma_ids(session: AsyncSession, lemma: str) -> set:
    threshold = settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD
    ocr_subq = select(OCRLemma.image_id).where(func.similarity(OCRLemma.lemma, lemma) >= threshold)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.similarity(ImageTag.value, lemma) >= threshold)
    result = await session.execute(union(ocr_subq, tag_subq))
    return {row[0] for row in result.all()}


async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.

    Each query lemma is matched exactly first; only if that finds nothing,
    and the lemma is at least settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH
    characters (avoiding short-word false positives — see the design doc's
    empirical similarity-score table), a trigram-similarity fallback
    (settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD) is tried instead. See
    docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md.
    """
    if not q:
        return None

    # language=None enables pymorphy3's script-based fallback (real Cyrillic
    # lemmatization) for a query string, which has no per-word language tag.
    # This is intentionally more thorough than the index side
    # (batch/utils/ocr_lemmas.py), which trusts each OCR row's own detected
    # language and skips lemmatization for confidently-non-Russian rows — see
    # that file's comment for the resulting (accepted) asymmetry.
    lemmas = normalize(
        q, _get_morph(),
        min_length=settings.BOW.MIN_WORD_LENGTH,
        language=None,
        keep_digit_tokens=True,
    )
    if not lemmas:
        return None

    matching_ids: Optional[set] = None
    for lemma in lemmas:
        lemma_ids = await _exact_lemma_ids(session, lemma)
        if not lemma_ids and len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH:
            lemma_ids = await _fuzzy_lemma_ids(session, lemma)

        matching_ids = lemma_ids if matching_ids is None else (matching_ids & lemma_ids)
        if not matching_ids:
            break

    return matching_ids
```

- [ ] **Step 4: Run to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v`
Expected: PASS, all tests in the file (the four new tests plus every pre-existing test —
confirms the refactor into `_exact_lemma_ids`/`_fuzzy_lemma_ids` didn't change behavior
for any existing case).

- [ ] **Step 5: Run the full four-root test sweep**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all four pass.

- [ ] **Step 6: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_ocr_lemmas_repository.py
git commit -m "feat: add trigram-similarity fuzzy-match fallback to search"
```

---

### Task 3: Rollout, index-usage verification, and real-data smoke test

**Files:** None (verification and rollout only).

- [ ] **Step 1: Apply the migration to `general` and `it`**

Requires each environment's `.env.<name>` file (gitignored — copy from the main repo
checkout into the worktree if working in one, same as prior rounds) and its
`DATABASE_URL` (see `environments/Environments.md`).

```powershell
# general
Get-Content ..\environments\.env.general | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic upgrade head

# it
Get-Content ..\environments\.env.it | foreach { $name, $value = $_.split('='); set-content env:\$name $value }
alembic upgrade head
```

Confirm both the same way as Task 1 Step 6 (`pg_indexes` query showing
`ix_ocr_lemmas_lemma_trgm`).

- [ ] **Step 2: Verify the trigram index is actually used, against real populated data**

This is the design doc's explicit verification requirement — confirm against at least
`metal` (213,981 rows, 46,879 distinct lemmas) that a realistic fuzzy-fallback query uses
the GIN index rather than a sequential scan:

```powershell
psql -h 127.0.0.1 -p 5432 -U ocr -d ocrdb -c "ANALYZE ocr_lemmas;"
psql -h 127.0.0.1 -p 5432 -U ocr -d ocrdb -c "EXPLAIN ANALYZE SELECT image_id FROM ocr_lemmas WHERE similarity(lemma, 'реклама') >= 0.35;"
```

Expected: the plan shows a `Bitmap Index Scan` (or `Index Scan`) referencing
`ix_ocr_lemmas_lemma_trgm`, not a `Seq Scan` on `ocr_lemmas`. If it shows a sequential
scan, do not treat this as acceptable-but-slower — investigate why the index isn't being
used (common causes: `ANALYZE` wasn't run so table statistics are stale, or the query
predicate isn't in a form Postgres's planner recognizes as index-compatible for this
operator class) before considering this task done. This is exactly the kind of "verify
against the installed system, don't assume" check applied to the SQLAlchemy
`join_transaction_mode` behavior and the SAVEPOINT mechanism in a prior round.

- [ ] **Step 3: Manual smoke test against a live server**

Start a scratch-port verification server (per the pattern established in the original
smart search rollout — pick a port outside the reserved 8081/8082/8083 table, verify
it's free first) against the real `metal` database, and confirm a real misspelled query
now returns results it wouldn't have before this feature:

```powershell
set PYTHONIOENCODING=utf-8
uvicorn Backend.app.main:app --env-file environments/.env.metal --port 8199 --host 127.0.0.1
```

Query `/api/images?q=<a real word from the metal corpus, deliberately misspelled by one
character>` and confirm it now returns the same results as querying the correctly-spelled
form, whereas before this feature it would have returned nothing. Stop the server
afterward and confirm the port is released.

- [ ] **Step 4: Final four-root sweep**

```bash
pytest tests/rules/ -v
pytest batch/tests/ -v
cd Backend && pytest && cd ..
```
```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```
Expected: all four pass.
