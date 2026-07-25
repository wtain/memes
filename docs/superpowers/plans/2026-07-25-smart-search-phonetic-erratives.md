# Smart Search: Phonetic Erratives Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let search queries containing Russian internet-slang erratives (e.g. "превед") find images whose OCR text/tags contain the canonical spelling ("привет"), without letting real dictionary words that happen to sound alike (e.g. "кот"/"код") cross-match each other.

**Architecture:** A ported Russian Metaphone algorithm (`rules/phonetic.py::russian_metaphone`) reduces a word to a phonetic code. Each `OCRLemma` row stores its own precomputed `phonetic_code` (new column + btree index). At query time, `matching_image_ids` unions in a phonetic lookup alongside the existing trigram fuzzy fallback — but only when the query lemma is Cyrillic, at least `PHONETIC_MIN_LEMMA_LENGTH` characters, and **not** a pymorphy3-recognized dictionary word (`is_known == False`). That last gate is what prevents real-word phonetic collisions from becoming search false positives — it's not needed for exact match or trigram, only for phonetic, because phonetic matching's false-positive class ("word" ↔️ same-sounding real word) is different from trigram's ("word" ↔️ typo of same word). A prerequisite fix in shared `rules/normalize.py` is required first — see Task 1.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 async ORM, PostgreSQL, Alembic, pymorphy3, pytest/pytest-asyncio.

## Global Constraints

- Ported Russian Metaphone algorithm must match `fonetika.metaphone.RussianMetaphone().transform()` (default flags) exactly — verified byte-for-byte during design against 29 test words; the same words are the unit test vocabulary in Task 2.
- `rules/normalize.py::lemmatize_word` must return `word.lower()` (not pymorphy3's guessed `normal_form`) when `parsed[0].is_known` is `False` — a prerequisite fix discovered during planning (see design doc's "Second empirical finding" section). Without it, `matching_image_ids` and the batch pipeline only ever see pymorphy3's guessed normal form, not the raw token, and the phonetic feature silently fails for erratives like `превед`/`аффтар` whose guessed lemma doesn't sound like the canonical word's lemma.
- `phonetic_code` column on `OCRLemma` is **nullable** (not `NOT NULL`) — deliberate, see the design doc's Storage section. Do not add a `NOT NULL` constraint or an ORM `@validates` hook that imports `rules/` into `Storage/models.py` (violates this project's layering: `Storage/models.py` sits below `rules/`/`repository/` per `CLAUDE.md`'s Architecture section).
- Phonetic matching queries `OCRLemma` only, never `ImageTag` (scope reduction — tags come from a controlled vocabulary and are essentially never themselves an errative string).
- New setting: `search.phonetic_min_lemma_length: 5` in `environments/settings.yaml`, alongside the existing `search.fuzzy_min_lemma_length: 5` / `search.fuzzy_similarity_threshold: 0.35`.
- Do **not** run `alembic upgrade` against the real `metal`/`general`/`it` databases as part of any task in this plan — those are the developer's always-running live environments (`environments/Environments.md`). Applying the migration there is an explicit, separate, user-approved step after this branch merges.
- `tests/rules/` and `tests/integration/` have separate `pytest.ini` files (different `asyncio_mode`) — per `CLAUDE.md`'s known gotchas, never run them in the same `pytest` invocation.

---

## File Structure

- Modify `rules/normalize.py` — prerequisite `lemmatize_word` fix (Task 1).
- Create `rules/phonetic.py` — the ported algorithm, pure function, no DB/settings dependency (Task 2).
- Create `tests/rules/test_phonetic.py` — unit tests for the algorithm (no DB, no I/O) (Task 2).
- Modify `Storage/models.py` — add `phonetic_code` column + index to `OCRLemma` (Task 3).
- Create `Storage/alembic/versions/<generated>_add_ocr_lemmas_phonetic_code.py` — schema + data-backfill migration (Task 3).
- Modify `repository/ocr_lemmas.py` — new `_is_known_word`/`_phonetic_lemma_ids` helpers, updated `matching_image_ids` fallback chain, updated `OCRLemmasSaver.add_lemmas` to populate `phonetic_code` (Task 4).
- Modify `environments/settings.yaml` — add `search.phonetic_min_lemma_length` (Task 4).
- Modify `tests/integration/test_ocr_lemmas_repository.py` — new end-to-end cases (Task 4).

---

### Task 1: Fix `lemmatize_word` to not guess a normal form for unrecognized words

**Files:**
- Modify: `rules/normalize.py` (`lemmatize_word`, currently at line 21)
- Test: `tests/rules/test_normalize.py`

**Interfaces:**
- Produces: `lemmatize_word`'s existing signature and public behavior are unchanged for every already-tested case (Russian known words, Latin passthrough, language-gated skip) — only the `is_known=False` sub-case of the `language is None` / `language in LEMMATIZABLE_LANGUAGES` branch changes. Task 2/3/4 depend on this: the phonetic feature only works correctly once erratives lemmatize to themselves instead of a guessed form.

- [ ] **Step 1: Write the failing tests**

In `tests/rules/test_normalize.py`, add a new test class after `TestLemmatizeWordLanguageGating` (keep the existing `from rules.normalize import lemmatize_word, make_morph, normalize` import — no new imports needed):

```python
class TestLemmatizeWordUnknownWordsStayAsTyped:
    """pymorphy3 runs its own unknown-word-guessing heuristics even for
    words it doesn't recognize, and that guess can invent letters that
    were never in the original word (e.g. "превед" -> "преведа"). For
    genuinely unrecognized words -- typos, internet-slang erratives,
    foreign fragments -- the word as typed is a more stable, predictable
    lemma than an unreliable guess with no real dictionary entry backing
    it. See docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md."""

    def test_preved_stays_unchanged(self):
        morph = make_morph()
        assert lemmatize_word("превед", morph) == "превед"

    def test_afftar_stays_unchanged(self):
        morph = make_morph()
        assert lemmatize_word("аффтар", morph) == "аффтар"

    def test_known_word_still_lemmatizes_normally(self):
        """Regression guard: the fix must only change is_known=False
        behavior -- known words still get their real normal_form."""
        morph = make_morph()
        assert lemmatize_word("работе", morph) == "работа"
```

- [ ] **Step 2: Run the tests to verify the first two fail**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_normalize.py -k TestLemmatizeWordUnknownWordsStayAsTyped -v`
Expected: `test_preved_stays_unchanged` and `test_afftar_stays_unchanged` FAIL (current behavior returns `"преведа"`/`"аффтара"`); `test_known_word_still_lemmatizes_normally` already PASSES (unaffected by the bug).

- [ ] **Step 3: Apply the fix**

In `rules/normalize.py`, the current `lemmatize_word` body reads:

```python
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()
```

Change to:

```python
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    if not parsed:
        return word.lower()
    if not parsed[0].is_known:
        # Words pymorphy3 doesn't recognize still get run through its
        # unknown-word guesser, which can invent a normal_form via
        # heuristic suffix-stripping (e.g. "превед" -> "преведа",
        # "аффтар" -> "аффтара") -- a guess with no real dictionary entry
        # backing it. The word as typed is a more stable, predictable
        # lemma for genuinely unrecognized words (typos, internet-slang
        # erratives, foreign fragments) than an invented guess.
        return word.lower()
    return parsed[0].normal_form
```

Also update the function's docstring — the current text describing the `language in LEMMATIZABLE_LANGUAGES` case reads `"language in LEMMATIZABLE_LANGUAGES (\"ru\"): real pymorphy3 lemmatization."`; change it to:

```
    language in LEMMATIZABLE_LANGUAGES ("ru"): real pymorphy3 lemmatization
    for recognized words; for words pymorphy3 doesn't recognize
    (is_known=False), returns word.lower() rather than pymorphy3's guessed
    normal_form -- see the is_known branch below for why.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_normalize.py -v`
Expected: PASS, every test in the file green (both the new class and every pre-existing test — confirming no regression to known-word lemmatization or the language-gating behavior).

- [ ] **Step 5: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "fix: stop pymorphy3's unknown-word guesser from mangling erratives' lemma"
```

---

### Task 2: Ported Russian Metaphone algorithm

**Files:**
- Create: `rules/phonetic.py`
- Test: `tests/rules/test_phonetic.py`

**Interfaces:**
- Produces: `rules.phonetic.russian_metaphone(word: str) -> str` and `rules.phonetic.is_cyrillic_word(word: str) -> bool`. Both are pure functions on already-lowercased input (callers, per `rules/normalize.py`'s `lemmatize_word`, only ever pass already-lowercase lemmas — `is_cyrillic_word` does not itself lowercase, and this is intentionally pinned by a test below).

- [ ] **Step 1: Write the failing unit tests**

Create `tests/rules/test_phonetic.py`:

```python
from rules.phonetic import is_cyrillic_word, russian_metaphone


class TestRussianMetaphoneErrativesCollapseToCanonicalForm:
    def test_preved_matches_privet(self):
        assert russian_metaphone("превед") == russian_metaphone("привет")

    def test_afftar_matches_avtor(self):
        assert russian_metaphone("аффтар") == russian_metaphone("автор")

    def test_krosavcheg_matches_krasavchik(self):
        assert russian_metaphone("кросавчег") == russian_metaphone("красавчик")

    def test_zhzhot_matches_zhzhet(self):
        assert russian_metaphone("жжот") == russian_metaphone("жжёт")


class TestRussianMetaphoneKnownFalsePositives:
    """These pairs are genuinely distinct dictionary words that still
    collapse to the same phonetic code -- documented, expected behavior of
    the algorithm itself. Callers gate on pymorphy3's is_known flag
    (repository/ocr_lemmas.py::_is_known_word) to avoid surfacing these as
    search results. This suite pins the exact codes so a future change to
    the algorithm can't silently drift without a test failing."""

    def test_kot_kod_collide(self):
        assert russian_metaphone("кот") == russian_metaphone("код") == "КАТ"

    def test_dom_dym_collide(self):
        assert russian_metaphone("дом") == russian_metaphone("дым") == "ДАМ"

    def test_stol_stal_collide(self):
        assert russian_metaphone("стол") == russian_metaphone("стал") == "СТАЛ"

    def test_parta_porta_collide(self):
        assert russian_metaphone("парта") == russian_metaphone("порта") == "ПАРТА"


class TestRussianMetaphoneExactCodes:
    """Pins exact output for words exercising each pipeline stage
    (devoicing, j-insertion, repeated-letter collapse), verified against
    fonetika's reference RussianMetaphone().transform() output during
    design."""

    def test_devoicing_word_final_and_before_vowel(self):
        assert russian_metaphone("любовь") == "ЛУБАФ"

    def test_j_insertion_after_vowel(self):
        assert russian_metaphone("объявление") == "АПJАВЛИНИJИ"

    def test_repeated_letter_collapse(self):
        assert russian_metaphone("жжот") == "ЖАТ"


class TestIsCyrillicWord:
    def test_pure_cyrillic_lowercase_true(self):
        assert is_cyrillic_word("превед") is True

    def test_latin_word_false(self):
        assert is_cyrillic_word("hello") is False

    def test_mixed_script_false(self):
        assert is_cyrillic_word("превedт") is False

    def test_uppercase_false(self):
        # matching_image_ids only ever passes already-lowercased lemmas
        # (see rules/normalize.py::lemmatize_word); this pins that
        # is_cyrillic_word does not itself lowercase.
        assert is_cyrillic_word("ПРЕВЕД") is False

    def test_empty_string_false(self):
        assert is_cyrillic_word("") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_phonetic.py -v`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'rules.phonetic'`.

- [ ] **Step 3: Implement `rules/phonetic.py`**

Create `rules/phonetic.py`:

```python
"""
Russian phonetic normalization, for erratives (deliberate internet-slang
misspellings like "превед", "аффтар") that don't share a spelling with
their canonical form but do sound alike.

Ported from the Russian Metaphone algorithm implemented by the `fonetika`
PyPI package (github.com/roddar92/russian_soundex, MIT), reimplemented
directly rather than taken as a dependency: fonetika's soundex module
unconditionally imports the unmaintained `pymorphy2` fork at import time,
which this project does not otherwise depend on (it uses pymorphy3).
Verified byte-for-byte against fonetika's reference output across a test
vocabulary of erratives, false-positive pairs, and general Russian words --
see docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md.

This module intentionally has no dependency on pymorphy3, settings, or the
DB layer -- callers (repository/ocr_lemmas.py) are responsible for gating
when a phonetic code is actually meaningful to compute or compare.
"""
import re

_CYRILLIC_WORD_RE = re.compile(r'^[а-яё]+$')

_CONSONANTS = 'бвгджзклмнпрстфхцчшщ'
_DEAF_VOWELS = 'аоыиэу'
_J_SEQ = r'^|ъ|ь'

_CONSONANT_VOWEL_MAP = [
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(я)'), r'\1а'),
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(ю)'), r'\1у'),
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(е)'), r'\1э'),
    (re.compile(r'(' + '|'.join(_CONSONANTS) + r')(ё)'), r'\1о'),
]
_J_MAP = [
    (re.compile(r'(' + _J_SEQ + r')(я)'), 'jа'),
    (re.compile(r'(' + _J_SEQ + r')(ю)'), 'jу'),
    (re.compile(r'(' + _J_SEQ + r')(е)'), 'jэ'),
    (re.compile(r'(' + _J_SEQ + r')(ё)'), 'jо'),
]
_VOWEL_J_MAP = [
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(я)'), r'\1jа'),
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(ю)'), r'\1jу'),
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(е)'), r'\1jэ'),
    (re.compile(r'(' + '|'.join(_DEAF_VOWELS) + r')(ё)'), r'\1jо'),
]
_REMOVE_SIGNS = [
    (re.compile(r'й'), 'j'),
    (re.compile(r'[ъь]'), ''),
]
_II_ENDING = re.compile(r'и[еио]')
_REDUCE_REPEATED = re.compile(r'(\w)(\1)+')

_VOWEL_BUCKET = str.maketrans('аяоыиеёэюу', 'ААААИИИИУУ')
_DEVOICE = str.maketrans('бздвг', 'пстфк')
_VOICED_CONSONANTS = set('бздвг')
_SONORANTS_AND_VOWELS = set('лмнр' + 'аяоыиеёэюу')


def is_cyrillic_word(word: str) -> bool:
    """True if word consists entirely of lowercase Russian Cyrillic
    letters -- the only input russian_metaphone() is meaningful for."""
    return bool(_CYRILLIC_WORD_RE.match(word))


def _apply_rules(word, rules):
    for pattern, replacement in rules:
        word = pattern.sub(replacement, word)
    return word


def _devoice_terminal_consonants(word):
    result = []
    for i, letter in enumerate(word):
        if letter in _VOICED_CONSONANTS and (
            i == len(word) - 1 or word[i + 1].lower() not in _SONORANTS_AND_VOWELS
        ):
            letter = letter.translate(_DEVOICE)
        result.append(letter)
    return ''.join(result)


def russian_metaphone(word: str) -> str:
    """
    Reduces a Russian word to a phonetic code: words that sound alike
    (including erratives and their canonical spelling) reduce to the same
    code. Also collapses some genuinely distinct dictionary words (e.g.
    "кот"/"код") -- callers must gate on pymorphy3's is_known flag to avoid
    over-matching real vocabulary; see the design doc.
    """
    word = word.lower()
    word = _apply_rules(word, _CONSONANT_VOWEL_MAP)
    word = _apply_rules(word, _J_MAP + _VOWEL_J_MAP)
    word = _apply_rules(word, _REMOVE_SIGNS)
    word = _II_ENDING.sub('и', word)
    word = _REDUCE_REPEATED.sub(r'\1', word)
    word = word.translate(_VOWEL_BUCKET)
    word = _devoice_terminal_consonants(word)
    return word.upper()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_phonetic.py -v`
Expected: PASS, all tests green.

- [ ] **Step 5: Commit**

```bash
git add rules/phonetic.py tests/rules/test_phonetic.py
git commit -m "feat: port Russian Metaphone algorithm for phonetic erratives matching"
```

---

### Task 3: `phonetic_code` column, index, and migration

**Files:**
- Modify: `Storage/models.py` (`OCRLemma` class, currently at line 227)
- Create: `Storage/alembic/versions/<generated-revision-id>_add_ocr_lemmas_phonetic_code.py`

**Interfaces:**
- Consumes: `rules.phonetic.russian_metaphone` (Task 2) — used by the migration's data-backfill step.
- Produces: `OCRLemma.phonetic_code` column (nullable `String`), `ix_ocr_lemmas_phonetic_code` btree index. Task 4 depends on both existing.

- [ ] **Step 1: Add the column and index to the ORM model**

In `Storage/models.py`, the current `OCRLemma` class reads:

```python
class OCRLemma(Base):
    __tablename__ = "ocr_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
        Index(
            "ix_ocr_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
    )

    image = relationship("Image", back_populates="ocr_lemmas")
```

Change it to:

```python
class OCRLemma(Base):
    __tablename__ = "ocr_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)
    # Nullable by design: only OCRLemmasSaver.add_lemmas() (the real write
    # path) populates it; rows created directly for tests unrelated to
    # phonetic matching are correctly inert with phonetic_code=NULL (NULL
    # never equals anything in SQL, so they never participate in phonetic
    # lookups). See docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md
    # for why this isn't a NOT NULL column or an ORM @validates hook.
    phonetic_code = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
        Index(
            "ix_ocr_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
        Index("ix_ocr_lemmas_phonetic_code", "phonetic_code"),
    )

    image = relationship("Image", back_populates="ocr_lemmas")
```

- [ ] **Step 2: Generate the Alembic revision**

Run (from `Storage/`, with `DATABASE_URL` set to the real `metal` env's connection string so Alembic can see current head — see `CLAUDE.md`'s Database migrations section for the exact `Get-Content ..\environments\.env.metal | ...` pattern):

```powershell
cd Storage
alembic revision -m "add_ocr_lemmas_phonetic_code"
```

This creates `Storage/alembic/versions/<hash>_add_ocr_lemmas_phonetic_code.py` with an empty `upgrade()`/`downgrade()` and `down_revision = '6fc209b37e8b'` (the current head, confirmed during design). Do **not** use `--autogenerate` — the data-backfill step below can't be autogenerated, and letting autogenerate touch the file risks pulling in the pre-existing, already-known `tmp_duplicates` drift (a raw-SQL-managed table unrelated to ORM models — see `CLAUDE.md`'s Alembic gotcha).

- [ ] **Step 3: Fill in the migration body**

Replace the generated file's `upgrade()`/`downgrade()` with:

```python
def upgrade() -> None:
    """Upgrade schema."""
    from rules.phonetic import is_cyrillic_word, russian_metaphone

    op.add_column("ocr_lemmas", sa.Column("phonetic_code", sa.String(), nullable=True))

    # Backfill by distinct lemma, not by row: a lemma repeats across many
    # (image_id, lemma) rows, and phonetic_code is a pure function of
    # lemma, so updating once per distinct lemma (rather than once per
    # row) is enough and scales with vocabulary size, not corpus size.
    connection = op.get_bind()
    distinct_lemmas = connection.execute(
        sa.text("SELECT DISTINCT lemma FROM ocr_lemmas")
    ).fetchall()
    for row in distinct_lemmas:
        if not is_cyrillic_word(row.lemma):
            continue  # stays NULL -- phonetic matching never applies to non-Cyrillic lemmas
        code = russian_metaphone(row.lemma)
        connection.execute(
            sa.text("UPDATE ocr_lemmas SET phonetic_code = :code WHERE lemma = :lemma"),
            {"code": code, "lemma": row.lemma},
        )

    op.create_index("ix_ocr_lemmas_phonetic_code", "ocr_lemmas", ["phonetic_code"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_ocr_lemmas_phonetic_code", table_name="ocr_lemmas")
    op.drop_column("ocr_lemmas", "phonetic_code")
```

(`op`, `sa` are already imported at the top of every generated migration file — leave the file's existing header, `revision`/`down_revision` identifiers, and imports as generated.)

- [ ] **Step 4: Verify the migration file loads and chains correctly**

Run (from `Storage/`):

```powershell
alembic history | Select-String "add_ocr_lemmas_phonetic_code"
```

Expected: one line showing the new revision with `6fc209b37e8b -> <new-hash>`, confirming it chains onto the current head without creating a branch.

Do **not** run `alembic upgrade head` against `metal`/`general`/`it` in this task (see Global Constraints) — applying it to those live databases happens later, as an explicit user-approved step after this branch merges. `tests/integration/`'s `db_engine` fixture builds its schema straight from `Storage/models.py` via `Base.metadata.create_all()` (not Alembic), so Task 4's integration tests exercise the model change directly and don't need the migration applied anywhere to pass.

- [ ] **Step 5: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/*_add_ocr_lemmas_phonetic_code.py
git commit -m "feat: add phonetic_code column and index to ocr_lemmas"
```

---

### Task 4: Query-time phonetic fallback, write-path population, settings, and integration tests

**Files:**
- Modify: `repository/ocr_lemmas.py`
- Modify: `environments/settings.yaml`
- Modify: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Consumes: `rules.phonetic.russian_metaphone`, `rules.phonetic.is_cyrillic_word` (Task 2); `OCRLemma.phonetic_code` (Task 3); the `lemmatize_word` fix (Task 1) — required for erratives to lemmatize predictably before phonetic matching runs.
- Produces: updated `matching_image_ids` behavior (phonetic fallback), updated `OCRLemmasSaver.add_lemmas` (populates `phonetic_code` on write).

- [ ] **Step 1: Add the new setting**

In `environments/settings.yaml`, the current `search` block reads:

```yaml
search:
  fuzzy_min_lemma_length: 5
  fuzzy_similarity_threshold: 0.35
```

Change it to:

```yaml
search:
  fuzzy_min_lemma_length: 5
  fuzzy_similarity_threshold: 0.35
  phonetic_min_lemma_length: 5
```

- [ ] **Step 2: Write the failing integration tests**

In `tests/integration/test_ocr_lemmas_repository.py`, add these test functions (append after the existing `test_no_similar_match_returns_empty_set`, keeping the existing imports — `OCRLemmasSaver` is already imported):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_errative_query_matches_canonical_form_via_phonetic_fallback(db_session):
    """"превед" (an errative) has no exact match to "привет", and their
    trigram similarity (0.167, verified against the real corpus during
    design) is well below settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD
    (0.35) -- only the phonetic path connects them."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"привет"})

    ids = await matching_image_ids(db_session, "превед")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_known_word_phonetic_collision_does_not_cross_match(db_session):
    """"полка" (shelf) and "палка" (stick) are both real dictionary words
    (is_known=True) that are already their own nominative-singular form
    (lemmatization leaves each unchanged) and phonetically collide (both
    reduce to "ПАЛКА"). Their trigram similarity (0.333, verified against
    the real corpus during design) is below the 0.35 threshold, so trigram
    doesn't already connect them either -- this isolates the is_known gate
    specifically: without it, phonetic matching alone would incorrectly
    connect these two unrelated real words. (An earlier candidate pair,
    "парта"/"порта", doesn't work for this: "порта" lemmatizes to "порт",
    which no longer collides with "парта"'s code at all.)"""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"палка"})

    ids = await matching_image_ids(db_session, "полка")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_short_lemma_does_not_reach_phonetic_fallback(db_session):
    """"жот" (3 chars) would phonetically collide with "жжот" (both reduce
    to "ЖАТ") if it reached the phonetic fallback at all, but its length is
    below both FUZZY_MIN_LEMMA_LENGTH and PHONETIC_MIN_LEMMA_LENGTH (both
    5 by default) -- it never attempts trigram or phonetic matching."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"жжот"})

    ids = await matching_image_ids(db_session, "жот")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_non_cyrillic_query_does_not_trigger_phonetic_matching(db_session):
    """Guards against russian_metaphone() being applied to non-Russian
    query tokens -- is_known(lemma) would also be False for most
    non-Cyrillic tokens (pymorphy3's dictionary is Russian-only), so
    without the explicit Cyrillic check every Latin-script query would
    otherwise attempt (meaningless) phonetic matching too."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    async with OCRLemmasSaver(db_session) as saver:
        await saver.add_lemmas(image.id, {"привет"})

    ids = await matching_image_ids(db_session, "hello")

    assert ids == set()
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run, with `DATABASE_URL` set per `CLAUDE.md`'s known gotcha for `tests/integration/`:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"
cd H:\workspace_sandbox\memes
.venv311\Scripts\pytest tests/integration/test_ocr_lemmas_repository.py -k "phonetic or non_cyrillic" -v
```

Expected: FAIL — `test_errative_query_matches_canonical_form_via_phonetic_fallback` fails because nothing connects "превед" to "привет" yet; `test_known_word_phonetic_collision_does_not_cross_match`, `test_short_lemma_does_not_reach_phonetic_fallback`, and `test_non_cyrillic_query_does_not_trigger_phonetic_matching` currently pass vacuously (no phonetic code path exists yet to produce a false positive) — that's expected and fine; they'll stay green through Step 5 and are regression tests for the behavior Step 4 adds.

- [ ] **Step 4: Implement the repository changes**

In `repository/ocr_lemmas.py`, the current imports read:

```python
from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, text, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.normalize import make_morph, normalize
from Storage.models import ImageTag, OCRLemma
```

Change to:

```python
from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, text, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.normalize import make_morph, normalize
from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import ImageTag, OCRLemma
```

Directly after `_fuzzy_lemma_ids` (before `matching_image_ids`), add:

```python
def _is_known_word(lemma: str) -> bool:
    """True if pymorphy3 recognizes lemma via genuine dictionary lookup, as
    opposed to falling back to its unknown-word-guessing analyzer. This is
    what separates erratives (is_known=False) from real dictionary words
    that happen to collide phonetically (is_known=True) -- see the design
    doc for the empirical basis."""
    return bool(_get_morph().parse(lemma)[0].is_known)


async def _phonetic_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    Phonetic-code fallback for erratives that trigram similarity cannot
    catch -- see docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md
    for the empirical case against trigram-only and phonetic-only
    approaches. Queries OCRLemma only, not ImageTag: tags come from a
    controlled tagging vocabulary and are essentially never themselves an
    errative string.
    """
    code = russian_metaphone(lemma)
    result = await session.execute(
        select(OCRLemma.image_id).where(OCRLemma.phonetic_code == code)
    )
    return {row[0] for row in result.all()}
```

Update `matching_image_ids`'s docstring and loop body. The current version reads:

```python
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

Change to:

```python
async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.

    Each query lemma is matched exactly first. If that finds nothing and
    the lemma is at least settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH characters
    (avoiding short-word false positives — see the design doc's empirical
    similarity-score table), a trigram-similarity fallback
    (settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD) is tried. See
    docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md.

    Additionally, when the lemma is Cyrillic, at least
    settings.SEARCH.PHONETIC_MIN_LEMMA_LENGTH characters, and not a
    pymorphy3-recognized dictionary word (_is_known_word is False), a
    phonetic-code fallback is unioned in on top of the trigram result --
    this catches erratives (deliberate misspellings like "превед") that
    trigram similarity cannot. The is_known gate is what prevents real
    dictionary words that happen to sound alike (e.g. "кот"/"код") from
    cross-matching via this path; trigram doesn't need the same gate
    because its own false-positive class is different (typo-of-same-word,
    not sounds-like-a-different-word). See
    docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md.
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
            if (
                is_cyrillic_word(lemma)
                and len(lemma) >= settings.SEARCH.PHONETIC_MIN_LEMMA_LENGTH
                and not _is_known_word(lemma)
            ):
                lemma_ids = lemma_ids | await _phonetic_lemma_ids(session, lemma)

        matching_ids = lemma_ids if matching_ids is None else (matching_ids & lemma_ids)
        if not matching_ids:
            break

    return matching_ids
```

Finally, update `OCRLemmasSaver.add_lemmas` so new rows get `phonetic_code` populated at write time. The current version reads:

```python
    async def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        if not lemmas:
            return
        stmt = (
            insert(OCRLemma)
            .values([{"image_id": image_id, "lemma": lemma} for lemma in lemmas])
            .on_conflict_do_nothing(index_elements=["image_id", "lemma"])
        )
        await self.session.execute(stmt)
```

Change to:

```python
    async def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        if not lemmas:
            return
        stmt = (
            insert(OCRLemma)
            .values([
                {
                    "image_id": image_id,
                    "lemma": lemma,
                    "phonetic_code": russian_metaphone(lemma) if is_cyrillic_word(lemma) else None,
                }
                for lemma in lemmas
            ])
            .on_conflict_do_nothing(index_elements=["image_id", "lemma"])
        )
        await self.session.execute(stmt)
```

- [ ] **Step 5: Run the full repository test file to verify everything passes**

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"
cd H:\workspace_sandbox\memes
.venv311\Scripts\pytest tests/integration/test_ocr_lemmas_repository.py -v
```

Expected: PASS, every test in the file green — both the pre-existing exact/trigram tests (proving no regression) and the four new phonetic tests from Step 2.

- [ ] **Step 6: Commit**

```bash
git add repository/ocr_lemmas.py environments/settings.yaml tests/integration/test_ocr_lemmas_repository.py
git commit -m "feat: add phonetic erratives fallback to smart search matching"
```

---

## After all tasks: manual verification note for the final whole-branch review

The final reviewer should confirm (by reading, not necessarily running against a live DB): the migration in Task 3 is never applied to `metal`/`general`/`it` as part of this branch's automated work, and a follow-up note is left for the user that after merge, they (or a future session, with explicit per-environment confirmation) need to run `alembic upgrade head` against each of the three real databases to get `phonetic_code` backfilled and phonetic search live in production.
