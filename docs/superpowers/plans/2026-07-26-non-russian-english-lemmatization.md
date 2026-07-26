# Non-Russian (English) Lemmatization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let search queries containing English word-form variants (e.g. "cats") find images whose OCR text/tags contain a different inflection of the same word ("cat"), without regressing Spanish search (also Latin-script, but explicitly out of scope for this pass).

**Architecture:** A lightweight rule-based stemmer (`snowballstemmer`, English) reduces a word to its stem. At index time, OCR rows already tagged `"en"` get their tokens stemmed directly via a new branch in `rules/normalize.py::lemmatize_word`. At query time, stemming is a new *fallback* tier in `repository/ocr_lemmas.py::matching_image_ids` — tried only after exact match fails, unioned alongside the existing trigram/phonetic fallback tiers, never replacing or preceding exact match. This asymmetry (stem at index time via an explicit language tag; only *fall back* to stemming at query time) is what keeps Spanish content — which has no per-token language tag at query time, and would otherwise get incorrectly routed through English-specific stemming rules — working exactly as it does today.

**Tech Stack:** Python 3.11, SQLAlchemy 2.0 async ORM, PostgreSQL, `snowballstemmer` (new dependency), pytest/pytest-asyncio.

## Global Constraints

- English only. Spanish is explicitly out of scope — do not add an `"es"` branch anywhere in this plan.
- `snowballstemmer` is the chosen library — zero dependencies, pure Python, 104KB (verified during design). Do not substitute spaCy or NLTK.
- `LEMMATIZABLE_LANGUAGES = frozenset({"ru"})` in `rules/normalize.py` must **not** be extended to include `"en"` — a new, separate `STEMMABLE_LANGUAGES = frozenset({"en"})` constant is added instead, since stemming and pymorphy3 lemmatization are different mechanisms.
- Stemming must **never** be applied to the primary `language=None` path inside `lemmatize_word` (i.e. `lemmatize_word`'s behavior for `language=None` is completely unchanged by this plan). It is added only as (a) an explicit `language == "en"`-gated branch inside `lemmatize_word` (used at index time, where each OCR row has its own reliable language tag), and (b) a new fallback tier inside `matching_image_ids` (used at query time, gated by `is_latin_word`, tried only after exact match already returns nothing). This split is the core regression-safety property of the whole design — see the design doc's "Query time" section for the full reasoning if anything here seems redundant.
- The new query-time fallback tier queries `OCRLemma` only, never `ImageTag` — same scope reduction as the existing phonetic fallback.
- Stemming has **no length guard** (unlike `FUZZY_MIN_LEMMA_LENGTH` for trigram/phonetic) — it applies regardless of query-word length.
- `Backend/requirements-backend.txt` must remain the full dependency closure, regenerated via a clean-venv `pip freeze` — never hand-edit individual version pins there.

---

## File Structure

- Create `rules/english_stemming.py` — the stemmer wrapper + Latin-script detection, pure functions, no DB/settings dependency (Task 1).
- Create `tests/rules/test_english_stemming.py` — unit tests (Task 1).
- Modify `Backend/requirements-backend.txt`, `requirements.txt` — add `snowballstemmer` (Task 1).
- Modify `rules/normalize.py` — add `STEMMABLE_LANGUAGES` constant + new `lemmatize_word` branch (Task 2).
- Modify `tests/rules/test_normalize.py` — new test class for the `"en"` branch (Task 2).
- Modify `repository/ocr_lemmas.py` — new `_stem_lemma_ids` helper, updated `matching_image_ids` loop (Task 3).
- Modify `tests/integration/test_ocr_lemmas_repository.py` — 4 new end-to-end cases (Task 3).

---

### Task 1: `snowballstemmer` dependency + `rules/english_stemming.py`

**Files:**
- Modify: `Backend/requirements-backend.txt`
- Modify: `requirements.txt`
- Create: `rules/english_stemming.py`
- Test: `tests/rules/test_english_stemming.py`

**Interfaces:**
- Produces: `rules.english_stemming.stem_english_word(word: str) -> str` and `rules.english_stemming.is_latin_word(word: str) -> bool`. Both are pure functions on already-lowercased-or-not input (`stem_english_word` lowercases internally; `is_latin_word` does not — it only recognizes already-lowercase input, matching `is_cyrillic_word`'s equivalent convention from the phonetic feature). Tasks 2 and 3 depend on both existing.

- [ ] **Step 1: Install the dependency in the local dev venv**

Run:
```powershell
H:\workspace_sandbox\memes\.venv311\Scripts\pip install snowballstemmer
```

- [ ] **Step 2: Add the dependency to root `requirements.txt`**

Add this line near the other NLP-related pins (`pymorphy3`, `wordfreq` — search for `pymorphy3==2.0.6` in the file and add the new line directly after the `pymorphy3-dicts-ru` line that follows it):

```
snowballstemmer==3.1.1
```

- [ ] **Step 3: Regenerate `Backend/requirements-backend.txt` via a clean venv**

This file's own header comment requires regenerating via a clean-venv `pip freeze` rather than hand-editing pins, so the file stays a correct full dependency closure. Its current header (everything before the first package line, `annotated-doc==0.0.4`) reads exactly:

```
# Runtime deps for the Backend API server.
# DO NOT add heavy ML/OCR/batch/GPU libs here (opencv, paddleocr, torch, etc.)
# — this is the single source of truth for both CI (backend tests/coverage/
# integration) and Dockerfile.backend (production image), and adding those
# would reintroduce the opencv/paddleocr conflict that requirements.txt has.
# pymorphy3 is a deliberate exception: it's a genuine runtime dependency of
# the query-time OCR-lemma search matching (repository/ocr_lemmas.py calls
# rules.normalize.normalize() on every search request), not a batch-only
# tool, and it's lightweight (pure Python + a dictionary data file) — it
# doesn't conflict with anything the way the CV/ML training stacks do.
# snowballstemmer is the same kind of exception, for the same reason: a
# genuine runtime dependency of the same query-time search path (the
# English-stemming fallback tier in repository/ocr_lemmas.py), and equally
# lightweight (pure Python, zero dependencies, 104KB).
# Dockerfile.backend builds wheels with --no-deps, so this list must be the
# full closure (direct + transitive) — regenerate via a clean-venv `pip freeze`
# after changing any pin here, don't hand-edit individual versions.
```

(Note the one added paragraph about `snowballstemmer`, mirroring the existing `pymorphy3` justification paragraph.)

Run, from the repo root:
```powershell
python -m venv temp_clean_venv
temp_clean_venv\Scripts\pip install --upgrade pip
temp_clean_venv\Scripts\pip install -r Backend\requirements-backend.txt
temp_clean_venv\Scripts\pip install snowballstemmer
temp_clean_venv\Scripts\pip freeze > Backend\requirements-backend-frozen.txt
Remove-Item -Recurse -Force temp_clean_venv
```

Then replace `Backend/requirements-backend.txt`'s contents with the header text above (with the new `snowballstemmer` paragraph), followed by the full contents of `Backend/requirements-backend-frozen.txt`. Delete `Backend/requirements-backend-frozen.txt` once merged in. Verify `snowballstemmer==3.1.1` appears somewhere in the final file.

- [ ] **Step 4: Write the failing unit tests**

Create `tests/rules/test_english_stemming.py`:

```python
from rules.english_stemming import is_latin_word, stem_english_word


class TestStemEnglishWordUnifiesInflections:
    def test_plural_noun(self):
        assert stem_english_word("cats") == stem_english_word("cat")

    def test_verb_conjugation(self):
        assert stem_english_word("running") == stem_english_word("run")

    def test_plural_with_spelling_change(self):
        assert stem_english_word("batteries") == stem_english_word("battery")

    def test_plural_compound_noun(self):
        assert stem_english_word("metalheads") == stem_english_word("metalhead")


class TestStemEnglishWordExactStems:
    """Pins exact stem output, verified against the real snowballstemmer
    package during design, so a future stemmer version bump can't
    silently drift without a test failing."""

    def test_cats(self):
        assert stem_english_word("cats") == "cat"

    def test_running(self):
        assert stem_english_word("running") == "run"

    def test_batteries(self):
        assert stem_english_word("batteries") == "batteri"

    def test_proper_noun_unchanged(self):
        assert stem_english_word("toronto") == "toronto"

    def test_proper_noun_unchanged_2(self):
        assert stem_english_word("hanneman") == "hanneman"


class TestStemEnglishWordKnownLimitation:
    """Documents, rather than hides, the accepted tradeoff of a stemmer
    over a full lemmatizer: irregular forms don't unify."""

    def test_irregular_adjective_does_not_unify(self):
        assert stem_english_word("better") != stem_english_word("good")


class TestIsLatinWord:
    def test_plain_lowercase_true(self):
        assert is_latin_word("cats") is True

    def test_cyrillic_false(self):
        assert is_latin_word("превед") is False

    def test_mixed_script_false(self):
        assert is_latin_word("catд") is False

    def test_hyphenated_false(self):
        # Deliberately narrow scope for this pass -- see the design doc's
        # disclosed limitations and the leftovers backlog's follow-up note.
        assert is_latin_word("well-known") is False

    def test_contraction_false(self):
        assert is_latin_word("don't") is False

    def test_digits_false(self):
        assert is_latin_word("covid19") is False

    def test_uppercase_false(self):
        # matching_image_ids only ever passes already-lowercased lemmas;
        # this pins that is_latin_word does not itself lowercase.
        assert is_latin_word("CATS") is False

    def test_empty_string_false(self):
        assert is_latin_word("") is False
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_english_stemming.py -v`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'rules.english_stemming'`.

- [ ] **Step 6: Implement `rules/english_stemming.py`**

Create `rules/english_stemming.py`:

```python
"""
Lightweight English word-form normalization for search matching, via the
`snowballstemmer` package (rule-based Porter/Snowball stemming -- no ML
model, no dictionary, zero dependencies). Not a full lemmatizer: it
doesn't produce a real dictionary word ("batteries" -> "batteri") and
won't unify irregular forms a POS-aware lemmatizer would ("better" stays
"better", not "good") -- an accepted tradeoff over a heavier dependency
(spaCy/NLTK), matching how OCRLemma.lemma was never guaranteed to be a
real word for Russian either. See
docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.
"""
import re

import snowballstemmer

_stemmer = snowballstemmer.stemmer("english")

_LATIN_WORD_RE = re.compile(r'^[a-z]+$')


def stem_english_word(word: str) -> str:
    return _stemmer.stemWord(word.lower())


def is_latin_word(word: str) -> bool:
    """True if word consists entirely of lowercase Latin letters -- the
    only input stem_english_word() is meaningful for. Deliberately narrow
    (no hyphens/apostrophes/digits): compounds like "well-known" and
    contractions like "don't" fall through unstemmed for now -- see the
    design doc's disclosed limitations."""
    return bool(_LATIN_WORD_RE.match(word))
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_english_stemming.py -v`
Expected: PASS, all tests green.

- [ ] **Step 8: Commit**

```bash
git add rules/english_stemming.py tests/rules/test_english_stemming.py requirements.txt Backend/requirements-backend.txt
git commit -m "feat: add snowballstemmer dependency and English stemming module"
```

---

### Task 2: `lemmatize_word` English dispatch branch

**Files:**
- Modify: `rules/normalize.py` (currently: `LEMMATIZABLE_LANGUAGES` at line 14, `lemmatize_word` at line 21)
- Test: `tests/rules/test_normalize.py`

**Interfaces:**
- Consumes: `rules.english_stemming.stem_english_word` (Task 1).
- Produces: `rules.normalize.STEMMABLE_LANGUAGES` (`frozenset({"en"})`); `lemmatize_word(word, morph, language="en")` now returns a stem instead of a lowercased passthrough. Task 3 depends on this for the index-time write path (via `batch/utils/ocr_lemmas.py` → `normalize()` → `lemmatize_word`, unchanged call sites, new behavior only for `"en"`-tagged rows).

- [ ] **Step 1: Write the failing test**

In `tests/rules/test_normalize.py`, add a new test class after `TestLemmatizeWordLanguageGating` (the file already imports `Mock` from `unittest.mock` for the existing `test_language_en_skips_pymorphy3_entirely` test — reuse it, no new imports needed):

```python
class TestLemmatizeWordStemmable:
    def test_language_en_stems_instead_of_lowercasing(self):
        morph = make_morph()
        assert lemmatize_word("cats", morph, language="en") == "cat"

    def test_language_en_does_not_call_pymorphy3(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("cats", wrapped, language="en")
        assert result == "cat"
        wrapped.parse.assert_not_called()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_normalize.py -k TestLemmatizeWordStemmable -v`
Expected: FAIL — currently `lemmatize_word("cats", morph, language="en")` returns `"cats"` (lowercased passthrough, since `"en"` is not in `LEMMATIZABLE_LANGUAGES`), not `"cat"`.

- [ ] **Step 3: Apply the change**

In `rules/normalize.py`, the current top of the file (after the subreddit regex) reads:

```python
# Languages pymorphy3 can meaningfully lemmatize — only Russian dictionaries are
# installed (pymorphy3-dicts-ru). There is no pymorphy3-dicts-uk in any
# requirements file today; add "uk" here if that ever changes.
LEMMATIZABLE_LANGUAGES = frozenset({"ru"})


def make_morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()
```

Change to:

```python
# Languages pymorphy3 can meaningfully lemmatize — only Russian dictionaries are
# installed (pymorphy3-dicts-ru). There is no pymorphy3-dicts-uk in any
# requirements file today; add "uk" here if that ever changes.
LEMMATIZABLE_LANGUAGES = frozenset({"ru"})

# Languages handled by a rule-based stemmer instead of a real dictionary
# lemmatizer -- a different mechanism than LEMMATIZABLE_LANGUAGES (pymorphy3),
# so kept as its own constant rather than folded into that one. See
# rules/english_stemming.py and
# docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.
STEMMABLE_LANGUAGES = frozenset({"en"})


def make_morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()
```

Also add this import near the top of the file, alongside the existing `import pymorphy3`:

```python
from rules.english_stemming import stem_english_word
```

The current `lemmatize_word` body reads:

```python
def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """
    language=None (default): unchanged legacy behavior — always call morph.parse(),
    relying on pymorphy3's own script-based fallback (real RU dictionary lookup for
    Cyrillic, LatinAnalyzer passthrough-lowercase for Latin script). Used by callers
    with no per-word language signal (concept/rules vocabulary loading, dev tools,
    tests).

    language is a string not in LEMMATIZABLE_LANGUAGES (including "unknown" for
    NULL/undetected OCR rows): pymorphy3 is skipped entirely; returns word.lower().
    Rows known, or assumed, not to be Russian never reach an analyzer that was never
    designed for them.

    language in LEMMATIZABLE_LANGUAGES ("ru"): real pymorphy3 lemmatization
    for recognized words; for words pymorphy3 doesn't recognize
    (is_known=False), returns word.lower() rather than pymorphy3's guessed
    normal_form -- see the is_known branch below for why.

    Note for callers outside this module (e.g. trends_batch's lemmatize_phrase): the
    None-means-"run pymorphy3 anyway" default here is a per-call fallback for callers
    with no language signal at all. A caller that already knows its own language ahead
    of time doesn't rely on this default — it simply never calls this function for
    non-lemmatizable content in the first place.
    """
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    if not parsed:
        return word.lower()
    if not parsed[0].is_known:
        return word.lower()
    return parsed[0].normal_form
```

Change to:

```python
def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """
    language in STEMMABLE_LANGUAGES ("en"): stems via
    rules.english_stemming.stem_english_word() instead of lowercasing.
    Checked first, before the LEMMATIZABLE_LANGUAGES gate below, since
    "en" would otherwise match "not in LEMMATIZABLE_LANGUAGES" and just
    get lowercased. This branch is index-time only in practice: each OCR
    row already carries its own detected language tag, so there's no
    ambiguity here the way there is at query time (see
    repository/ocr_lemmas.py's separate, query-time-only stemming
    fallback tier, gated by is_latin_word rather than an explicit
    language tag, and deliberately NOT wired into this function's
    language=None path -- see
    docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md
    for why: Spanish is also Latin-script, and this function has no way
    to distinguish it from English by language tag alone at query time).

    language=None (default): unchanged legacy behavior — always call morph.parse(),
    relying on pymorphy3's own script-based fallback (real RU dictionary lookup for
    Cyrillic, LatinAnalyzer passthrough-lowercase for Latin script). Used by callers
    with no per-word language signal (concept/rules vocabulary loading, dev tools,
    tests, and query-time search matching).

    language is a string not in LEMMATIZABLE_LANGUAGES or STEMMABLE_LANGUAGES
    (including "unknown" for NULL/undetected OCR rows, and "es" for Spanish):
    pymorphy3 is skipped entirely; returns word.lower(). Rows known, or assumed,
    not to be Russian or English never reach an analyzer that was never designed
    for them.

    language in LEMMATIZABLE_LANGUAGES ("ru"): real pymorphy3 lemmatization
    for recognized words; for words pymorphy3 doesn't recognize
    (is_known=False), returns word.lower() rather than pymorphy3's guessed
    normal_form -- see the is_known branch below for why.

    Note for callers outside this module (e.g. trends_batch's lemmatize_phrase): the
    None-means-"run pymorphy3 anyway" default here is a per-call fallback for callers
    with no language signal at all. A caller that already knows its own language ahead
    of time doesn't rely on this default — it simply never calls this function for
    non-lemmatizable content in the first place.
    """
    if language in STEMMABLE_LANGUAGES:
        return stem_english_word(word)
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    if not parsed:
        return word.lower()
    if not parsed[0].is_known:
        return word.lower()
    return parsed[0].normal_form
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_normalize.py -v`
Expected: PASS, every test in the file green (both the new class and every pre-existing test — confirming no regression to Russian lemmatization or the other language-gating branches).

- [ ] **Step 5: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: stem English-tagged tokens instead of lowercasing in lemmatize_word"
```

---

### Task 3: Query-time stemming fallback and integration tests

**Files:**
- Modify: `repository/ocr_lemmas.py`
- Modify: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Consumes: `rules.english_stemming.stem_english_word`, `rules.english_stemming.is_latin_word` (Task 1); the `language == "en"` index-time dispatch (Task 2) — this task's tests rely on `OCRLemmasSaver.add_lemmas` already storing stems correctly for `"en"`-tagged content, which Task 2 already guarantees end-to-end via `normalize()`.
- Produces: updated `matching_image_ids` behavior (English-stemming fallback tier).

- [ ] **Step 1: Write the failing integration tests**

In `tests/integration/test_ocr_lemmas_repository.py`, add these test functions (append after the existing `test_no_similar_match_returns_empty_set` — or after the last phonetic test if this file has grown further since; keep the existing imports, `OCRLemma` is already imported):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_english_stem_fallback_matches_different_inflection(db_session):
    """"cats" (query) matches an image indexed with lemma "cat" -- the
    stem OCRLemmasSaver.add_lemmas() would have stored for an
    "en"-tagged OCR row containing "cats" (stem_english_word produces the
    same "cat" stem for both "cats" and "cat")."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="cat"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "cats")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_spanish_word_still_matches_via_exact_match_unaffected_by_stemming(db_session):
    """Regression guard for the exact concern that drove the query-time
    design: a Spanish word indexed and queried identically must still
    match via exact match, unaffected by the new English-stemming
    fallback existing at all. Spanish is also Latin-script
    (is_latin_word("gatos") is True), so this only passes if exact match
    genuinely wins before the stemming fallback ever gets a chance to
    run."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="gatos"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "gatos")

    assert ids == {image.id}


@pytest.mark.asyncio(loop_scope="session")
async def test_spanish_word_without_exact_match_does_not_spuriously_match_via_stemming(db_session):
    """If a Spanish query word has no exact match, the stemming fallback
    still runs (is_latin_word can't distinguish Spanish from English --
    stem_english_word("gatos") == "gato") -- but since nothing in the
    index has "gato" as its lemma, this must not produce a spurious
    match. Verifies the design doc's "harmless, not wrong" claim about
    the fallback running on non-English Latin-script content: it can
    only add correct matches, never a false one."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="perro"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "gatos")

    assert ids == set()


@pytest.mark.asyncio(loop_scope="session")
async def test_short_english_word_still_reaches_stemming_fallback(db_session):
    """Stemming has no length guard (unlike trigram/phonetic's
    FUZZY_MIN_LEMMA_LENGTH) -- it's deterministic suffix-stripping, not a
    similarity search, so it doesn't carry the same short-word
    false-positive risk. "run" (3 chars) is well below
    settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH (5) but must still reach the
    stemming fallback."""
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()
    db_session.add(OCRLemma(image_id=image.id, lemma="run"))
    await db_session.flush()

    ids = await matching_image_ids(db_session, "runs")

    assert ids == {image.id}
```

- [ ] **Step 2: Run the new tests to verify expected pre-implementation state**

Run, with `DATABASE_URL` set per `CLAUDE.md`'s known gotcha for `tests/integration/`:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"
cd H:\workspace_sandbox\memes
.venv311\Scripts\pytest tests/integration/test_ocr_lemmas_repository.py -k "english_stem or spanish or short_english" -v
```

Expected: `test_english_stem_fallback_matches_different_inflection` and `test_short_english_word_still_reaches_stemming_fallback` FAIL (no stemming fallback exists yet, so "cats"/"runs" don't reach "cat"/"run"); `test_spanish_word_still_matches_via_exact_match_unaffected_by_stemming` and `test_spanish_word_without_exact_match_does_not_spuriously_match_via_stemming` already PASS (both are testing behavior that doesn't depend on the new code existing) — that's expected; they'll stay green through Step 4 as regression guards.

- [ ] **Step 3: Implement the repository changes**

In `repository/ocr_lemmas.py`, the current imports read:

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

Change to:

```python
from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, text, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.english_stemming import is_latin_word, stem_english_word
from rules.normalize import make_morph, normalize
from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import ImageTag, OCRLemma
```

Directly after `_phonetic_lemma_ids` (before `matching_image_ids`), add:

```python
async def _stem_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    Query-time-only fallback for English word-form variation (e.g. a
    query for "cats" reaching an indexed "cat"). OCRLemmasSaver.add_lemmas()
    already stores the *stemmed* form for "en"-tagged OCR rows (via
    lemmatize_word's STEMMABLE_LANGUAGES branch), so an exact match
    against the query's own stem is enough here -- no separate storage or
    index needed.

    Tried only after exact match already fails (see matching_image_ids),
    mirroring the trigram/phonetic fallback pattern -- NOT baked into the
    primary lemma path, to avoid stemming Spanish (also Latin-script)
    query tokens with English-specific rules and breaking exact match for
    Spanish content that works today. See
    docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.

    OCRLemma only, not ImageTag -- same scope reduction as the phonetic
    fallback (tags are a controlled vocabulary, not raw OCR text).
    """
    stem = stem_english_word(lemma)
    result = await session.execute(
        select(OCRLemma.image_id).where(OCRLemma.lemma == stem)
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

Change to:

```python
async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index or tags contain every query lemma (AND); an empty set
    means no image matches.

    Each query lemma is matched exactly first. If that finds nothing,
    every applicable fallback tier below is unioned together (not tried
    sequentially with early exit) -- they catch different failure classes,
    so there's no reason one should suppress another:

    - If the lemma is Latin-script (is_latin_word), an English-stemming
      fallback (stem_english_word) is tried, with no length guard --
      it's deterministic suffix-stripping, not a similarity search, so it
      doesn't carry the short-word false-positive risk that motivates a
      length guard elsewhere. Deliberately NOT applied to the lemma's
      primary normalization (see rules/normalize.py::lemmatize_word) --
      only as this query-time fallback -- because Spanish is also
      Latin-script and would otherwise get incorrectly stemmed with
      English-specific rules. See
      docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.
    - If the lemma is at least settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH
      characters (avoiding short-word false positives — see the design
      doc's empirical similarity-score table), a trigram-similarity
      fallback (settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD) is tried. See
      docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md.
    - Additionally, when the lemma is Cyrillic, at least
      settings.SEARCH.PHONETIC_MIN_LEMMA_LENGTH characters, and not a
      pymorphy3-recognized dictionary word (_is_known_word is False), a
      phonetic-code fallback is unioned in too -- this catches erratives
      (deliberate misspellings like "превед") that trigram similarity
      cannot. See
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
        if not lemma_ids:
            if is_latin_word(lemma):
                lemma_ids = lemma_ids | await _stem_lemma_ids(session, lemma)
            if len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH:
                lemma_ids = lemma_ids | await _fuzzy_lemma_ids(session, lemma)
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

- [ ] **Step 4: Run the full repository test file to verify everything passes**

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"
cd H:\workspace_sandbox\memes
.venv311\Scripts\pytest tests/integration/test_ocr_lemmas_repository.py -v
```

Expected: PASS, every test in the file green — the pre-existing exact/trigram/phonetic tests (proving no regression) and all 4 new tests from Step 1.

- [ ] **Step 5: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_ocr_lemmas_repository.py
git commit -m "feat: add English-stemming fallback to smart search matching"
```

---

## After all tasks: production data note

Unlike the phonetic-erratives feature, this one needs **no** database migration or
schema change — `OCRLemma.lemma` already exists and this feature only changes what
string gets computed and stored in it. However, existing `"en"`-tagged rows already
indexed in `metal`/`general`/`it` were indexed *before* this feature existed, so they
still store lowercased (unstemmed) forms, not stems. For the stemming fallback to find
anything for previously-indexed English content, `batch/build_ocr_lemmas.py` needs a
full rebuild (no `--incremental`) per environment after this branch merges — the same
operational step already performed for the `lang_score` cleanup earlier in this
project's history. This is a live-data operation and needs the user's explicit
go-ahead per environment, same as that earlier rebuild; the final reviewer should flag
it as a follow-up, not attempt it automatically.
