# Lemmatization Uniformity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `rules/normalize.py`'s pymorphy3-backed lemmatization punctuation-aware and language-gated, then reuse it (via the new `LEMMATIZABLE_LANGUAGES` constant) to merge inflected Russian entity names in `trends_batch.py`'s trend counting — implementing three approved specs as one coordinated change since all three touch `rules/normalize.py`.

**Architecture:** `rules/normalize.py` stays the single shared normalization module (per CLAUDE.md). It gains: a punctuation-preserving `tokenize()`, an optional `language` parameter on `lemmatize_word()`/`normalize()` gated by a new `LEMMATIZABLE_LANGUAGES` constant, and a new `lemmatize_phrase()` for ordered multi-word phrases. Downstream OCR consumers (`build_bow.py`, `concept_tagger.py` via `build_tags_from_ocr.py`) thread their already-known per-row `language` through. `trends_batch.py` reuses the same constant to decide when to call the new `lemmatize_phrase()`. `ImagesRepository` gets two **new** methods rather than modified existing ones, since 9 other call sites and 2 existing tests depend on the current 5-tuple row shape.

**Tech Stack:** Python 3.11 (`.venv311`), `pymorphy3` 2.0.6 + `pymorphy3-dicts-ru` (Russian only — no Ukrainian dict installed), SQLAlchemy async ORM, pytest / pytest-asyncio.

## Global Constraints

- Target environment for all batch/backend code: Python 3.11 via `.venv311` (per CLAUDE.md).
- No new dependencies. Only `pymorphy3-dicts-ru` is installed (verified: `requirements.txt` has no `pymorphy3-dicts-uk`).
- `LEMMATIZABLE_LANGUAGES = frozenset({"ru"})` (in `rules/normalize.py`) is the single source of truth for "which languages can this module's pymorphy3 lemmatizer meaningfully handle." Never hardcode a bare `== "ru"` / `in {"ru"}` check anywhere else — import and reuse the constant.
- **Never modify `ImagesRepository.get_images_and_ocr_texts()` or `get_images_and_ocr_texts_without_tags()` in place.** Nine existing call sites (`batch/diff_rules.py`, seven `batch/tools/spot_check_*.py` scripts) and two existing integration tests destructure their rows as 5-tuples. Add new `*_with_language()` methods instead; leave the originals byte-for-byte untouched.
- No data migration and no backfill of already-stored data (`ocr_texts.language`/`lang_score` values, or existing `trends_run_results` rows) — every change here is forward-looking only, affecting how new batch runs compute output.
- Do **not** run full production batch jobs (`build_bow`, `build_tags_from_ocr`, `trends_batch`) against real `metal`/`general`/`it` environments as part of executing this plan — that re-processes real data against real, always-running environments (see `environments/Environments.md`) and is a manual operational step for the user to run after reviewing the code, not something to automate unsupervised.
- Test commands used throughout (per CLAUDE.md, confirmed working in this repo):
  - No-DB suites: `pytest tests/rules/ tests/batch/` (run from repo root; takes ~2 minutes due to model-loading imports — this is expected, not a hang).
  - DB-backed suites: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v` (requires a live Postgres with pgvector on the `ocrdb_test` database — see `tests/integration/conftest.py`).
  - Do not combine either of the above with `cd Backend && pytest` or `pytest batch/tests/` in one invocation (separate `pytest.ini` files, different `asyncio_mode` — see CLAUDE.md's Known Gotchas).

---

## Task 1: `tokenize()` punctuation preservation

**Files:**
- Modify: `rules/normalize.py:21-24` (the `tokenize()` function)
- Create: `tests/rules/test_normalize.py`

**Interfaces:**
- Consumes: nothing new (uses stdlib `re`, already imported)
- Produces: `tokenize(text: str) -> list[str]` — same signature as before, new behavior: a single `-` or `'` between two word-character runs stays part of the token; em-dash `—`/en-dash `–` and the curly apostrophe `'` are normalized to `-`/`'` before tokenizing. Underscore still splits (unchanged).

- [ ] **Step 1: Write the failing tests**

Create `tests/rules/test_normalize.py`:

```python
from rules.normalize import tokenize


class TestTokenizeHyphenPreservation:
    def test_hyphenated_compound_stays_joined(self):
        assert tokenize("Санкт-Петербурга") == ["Санкт-Петербурга"]

    def test_double_hyphen_splits(self):
        assert tokenize("Санкт--Петербурга") == ["Санкт", "Петербурга"]

    def test_leading_hyphen_not_consumed(self):
        assert tokenize("-leading") == ["leading"]

    def test_trailing_hyphen_not_consumed(self):
        assert tokenize("trailing-") == ["trailing"]

    def test_digit_containing_compound_joins_too(self):
        assert tokenize("covid-19") == ["covid-19"]


class TestTokenizeApostrophePreservation:
    def test_contraction_stays_joined(self):
        assert tokenize("don't") == ["don't"]

    def test_name_with_apostrophe_stays_joined(self):
        assert tokenize("O'Brien") == ["O'Brien"]

    def test_trailing_quote_not_joined(self):
        assert tokenize("YOLO'") == ["YOLO"]

    def test_leading_quote_not_joined(self):
        assert tokenize("'sup") == ["sup"]

    def test_closing_quote_role_resolved_correctly(self):
        assert tokenize("it's a 'quote' test") == ["it's", "a", "quote", "test"]


class TestTokenizeJoinerNormalization:
    def test_em_dash_normalized_to_hyphen(self):
        assert tokenize("well—known") == ["well-known"]

    def test_en_dash_normalized_to_hyphen(self):
        assert tokenize("well–known") == ["well-known"]

    def test_curly_apostrophe_normalized(self):
        assert tokenize("don’t") == ["don't"]


class TestTokenizeUnderscoreStillSplits:
    def test_underscore_handle_splits(self):
        assert tokenize("varg_vikernes") == ["varg", "vikernes"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: FAIL on every hyphen/apostrophe/dash test (current `tokenize()` splits on all punctuation) — the underscore test should already PASS (unaffected by this change).

- [ ] **Step 3: Implement the regex change**

In `rules/normalize.py`, replace the `tokenize()` function (currently lines 21-24):

```python
def tokenize(text: str) -> list[str]:
    # [^\W_] = letters and digits only; underscores treated as delimiters so that
    # social-media handles like "varg_vikernes" split into ["varg", "vikernes"].
    return re.findall(r'[^\W_]+', text, re.UNICODE)
```

with:

```python
_TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)

_JOINER_NORMALIZE = str.maketrans({
    "–": "-",   # en dash
    "—": "-",   # em dash
    "’": "'",   # right single quotation mark / smart apostrophe
})


def _normalize_joiners(text: str) -> str:
    return text.translate(_JOINER_NORMALIZE)


def tokenize(text: str) -> list[str]:
    # [^\W_] = letters and digits only; underscores treated as delimiters so that
    # social-media handles like "varg_vikernes" split into ["varg", "vikernes"].
    # A single '-' or "'" between two word-character runs stays part of the token
    # (compounds like "Санкт-Петербурга", contractions like "don't"); every other
    # occurrence of either character — with no word character immediately
    # following — still splits/strips as before. Em/en dashes and the curly
    # apostrophe are normalized to their ASCII counterparts first so there's one
    # canonical joiner per type.
    return _TOKEN_RE.findall(_normalize_joiners(text))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Run the full rules test suite to check for regressions**

Run: `pytest tests/rules/ -v`
Expected: PASS. `tests/rules/test_concept_tagger.py` and `tests/rules/test_engine.py` should be unaffected — `test_engine.py`'s `RulesEngine` has its own separate `\w+` regex (per the tokenize spec's investigation), and `test_concept_tagger.py`'s existing cases don't use hyphenated/apostrophe'd text.

- [ ] **Step 6: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: preserve internal hyphens/apostrophes in tokenize()"
```

---

## Task 2: `LEMMATIZABLE_LANGUAGES` + language-gated `lemmatize_word()`/`normalize()`

**Files:**
- Modify: `rules/normalize.py` (add constant, extend two function signatures)
- Modify: `tests/rules/test_normalize.py` (append tests)

**Interfaces:**
- Consumes: `tokenize()` from Task 1 (unchanged call sites within `normalize()`)
- Produces: `LEMMATIZABLE_LANGUAGES: frozenset[str]`; `lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str`; `normalize(text: str, morph: pymorphy3.MorphAnalyzer, min_length: int = 3, language: str | None = None) -> set[str]`. Both `language` params default to `None`, preserving today's behavior for every existing caller.

- [ ] **Step 1: Write the failing tests**

Append to `tests/rules/test_normalize.py`:

```python
from unittest.mock import Mock

from rules.normalize import lemmatize_word, make_morph, normalize


class TestLemmatizeWordLanguageGating:
    def test_language_none_lemmatizes_russian_word_as_before(self):
        morph = make_morph()
        assert lemmatize_word("работе", morph) == "работа"

    def test_language_none_lowercases_latin_word_as_before(self):
        morph = make_morph()
        assert lemmatize_word("RUNNING", morph) == "running"

    def test_language_ru_lemmatizes_normally(self):
        morph = make_morph()
        assert lemmatize_word("Путина", morph, language="ru") == "путин"

    def test_language_en_skips_pymorphy3_entirely(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("RUNNING", wrapped, language="en")
        assert result == "running"
        wrapped.parse.assert_not_called()

    def test_language_es_skips_pymorphy3_entirely(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("CANCIÓN", wrapped, language="es")
        assert result == "canción"
        wrapped.parse.assert_not_called()

    def test_language_unknown_skips_pymorphy3_entirely(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = lemmatize_word("MYSTERY", wrapped, language="unknown")
        assert result == "mystery"
        wrapped.parse.assert_not_called()


class TestNormalizeLanguageGating:
    def test_language_none_reproduces_default_behavior(self):
        morph = make_morph()
        assert normalize("работе сегодня", morph) == {"работа", "сегодня"}

    def test_language_en_lowercases_without_pymorphy3(self):
        morph = make_morph()
        wrapped = Mock(wraps=morph)
        result = normalize("RUNNING FAST", wrapped, language="en")
        assert result == {"running", "fast"}
        wrapped.parse.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: FAIL with `TypeError: lemmatize_word() got an unexpected keyword argument 'language'` (and similarly for `normalize()`).

- [ ] **Step 3: Implement the language gating**

In `rules/normalize.py`, add the constant after `_SUBREDDIT_OCR_RE` (before `make_morph()`):

```python
# Languages pymorphy3 can meaningfully lemmatize — only Russian dictionaries are
# installed (pymorphy3-dicts-ru). There is no pymorphy3-dicts-uk in any
# requirements file today; add "uk" here if that ever changes.
LEMMATIZABLE_LANGUAGES = frozenset({"ru"})
```

Replace `lemmatize_word()`:

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

    language in LEMMATIZABLE_LANGUAGES ("ru"): real pymorphy3 lemmatization.

    Note for callers outside this module (e.g. trends_batch's lemmatize_phrase): the
    None-means-"run pymorphy3 anyway" default here is a per-call fallback for callers
    with no language signal at all. A caller that already knows its own language ahead
    of time doesn't rely on this default — it simply never calls this function for
    non-lemmatizable content in the first place.
    """
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()
```

Replace `normalize()`:

```python
def normalize(
    text: str,
    morph: pymorphy3.MorphAnalyzer,
    min_length: int = 3,
    language: str | None = None,
) -> set[str]:
    """Tokenize, drop short tokens and pure digits, return lemma set."""
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length or word.isdigit():
            continue
        lemma = lemmatize_word(word, morph, language)
        result.add(lemma)
        # r/subreddit OCR artifact: "r/Metallica" → "rimetallica" (slash read as 'i')
        m = _SUBREDDIT_OCR_RE.match(word)
        if m:
            suffix = m.group(1)
            if len(suffix) >= min_length:
                result.add(lemmatize_word(suffix, morph, language))
        # Trailing punctuation artifact: "SLAYER!!" → "slayerll" (!! read as ll)
        # Strip doubled trailing letter and emit the shorter form.
        if len(word) > min_length + 1 and word[-1].isalpha() and word[-1] == word[-2]:
            shorter = word[:-2]
            if len(shorter) >= min_length:
                result.add(lemmatize_word(shorter, morph, language))
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: PASS (23 tests total: 14 from Task 1 + 9 new)

- [ ] **Step 5: Run the full rules test suite to check for regressions**

Run: `pytest tests/rules/ -v`
Expected: PASS — every existing caller of `lemmatize_word()`/`normalize()` (in `rules/concept_tagger.py`, `batch/build_bow.py`, tested via `tests/rules/test_concept_tagger.py`) calls with no `language` argument, so the new parameter's default preserves their behavior exactly.

- [ ] **Step 6: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: add LEMMATIZABLE_LANGUAGES gate to lemmatize_word()/normalize()"
```

---

## Task 3: `lemmatize_phrase()` for ordered multi-word phrases

**Files:**
- Modify: `rules/normalize.py` (add new function)
- Modify: `tests/rules/test_normalize.py` (append tests)

**Interfaces:**
- Consumes: `lemmatize_word()` from Task 2 (called with no `language` arg — see rationale below)
- Produces: `lemmatize_phrase(text: str, morph: pymorphy3.MorphAnalyzer) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/rules/test_normalize.py`:

```python
from rules.normalize import lemmatize_phrase


class TestLemmatizePhrase:
    def test_single_word(self):
        morph = make_morph()
        assert lemmatize_phrase("Путина", morph) == "путин"

    def test_multi_word_phrase_normalizes_each_word(self):
        morph = make_morph()
        assert lemmatize_phrase("Владимира Путина", morph) == "владимир путин"

    def test_hyphenated_compound_stays_joined_and_lemmatizes_as_one(self):
        morph = make_morph()
        assert lemmatize_phrase("Санкт-Петербурга", morph) == "санкт-петербург"

    def test_already_nominative_input_is_idempotent(self):
        morph = make_morph()
        assert lemmatize_phrase("Владимир Путин", morph) == "владимир путин"

    def test_preserves_word_order(self):
        morph = make_morph()
        result = lemmatize_phrase("Владимира Путина", morph)
        assert result.split() == ["владимир", "путин"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: FAIL with `ImportError: cannot import name 'lemmatize_phrase'`.

- [ ] **Step 3: Implement `lemmatize_phrase()`**

In `rules/normalize.py`, add after `normalize()`:

```python
def lemmatize_phrase(text: str, morph: pymorphy3.MorphAnalyzer) -> str:
    """Lemmatize each whitespace-delimited chunk of text, preserving
    internal punctuation (e.g. hyphens in compound names) and word order."""
    return " ".join(lemmatize_word(chunk, morph) for chunk in text.split())
```

Note: this deliberately calls `lemmatize_word(chunk, morph)` with no `language` argument — it always performs real Russian lemmatization. Callers are expected to only invoke `lemmatize_phrase()` when they already know the text is Russian (see Task 10); it does not do its own gating, unlike `lemmatize_word()`/`normalize()`.

It also deliberately does not reuse `tokenize()` — `tokenize()` drops punctuation and word order, which is fine for bag-of-words but wrong for a phrase that needs to stay a single, ordered, readable string (e.g. not fragmenting "Санкт-Петербург" across its hyphen). `text.split()` (whitespace-only) preserves that.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -v`
Expected: PASS (28 tests total)

- [ ] **Step 5: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: add lemmatize_phrase() for ordered multi-word lemmatization"
```

---

## Task 4: Thread `language` through `build_bow.py`

**Files:**
- Modify: `batch/build_bow.py:171` (inside `_build_ocr_bow`)
- Modify: `tests/integration/test_build_ocr_bow_lang_filter.py`

**Interfaces:**
- Consumes: `lemmatize_word(word, morph, language)` from Task 2
- Produces: no new public interface — `_build_ocr_bow()`'s signature is unchanged

- [ ] **Step 1: Write the failing test**

In `tests/integration/test_build_ocr_bow_lang_filter.py`, add the import and a new test function:

```python
from unittest.mock import Mock
```

(add alongside the existing `import uuid` / `import pytest` imports at the top)

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_build_ocr_bow_skips_pymorphy3_for_non_russian_language(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    db_session.add(
        OCRText(
            image_id=image.id,
            text="genuine spanish words aqui",
            confidence=0.9,
            language="es",
            lang_score=1.0,
        )
    )
    await db_session.flush()

    morph = make_morph()
    wrapped_morph = Mock(wraps=morph)
    metrics = SimpleMetricsListener()
    output = await _build_ocr_bow(
        db_session,
        wrapped_morph,
        confidence_min=0.4,
        lang_score_min=0.3,
        min_word_length=3,
        min_frequency=1,
        metrics=metrics,
    )

    wrapped_morph.parse.assert_not_called()
    es_lemmas = output.get("es", {})
    assert "genuine" in es_lemmas
    assert "aqui" in es_lemmas
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_ocr_bow_lang_filter.py -v`
Expected: FAIL — `wrapped_morph.parse.assert_not_called()` raises `AssertionError` because today's code calls `morph.parse()` unconditionally regardless of `language`.

- [ ] **Step 3: Implement the fix**

In `batch/build_bow.py`, inside `_build_ocr_bow` (around line 171), change:

```python
        lang = language or "unknown"
        for word in tokenize(text):
            if len(word) < min_word_length or word.isdigit():
                continue
            lang_counters[lang][lemmatize_word(word, morph)] += 1
```

to:

```python
        lang = language or "unknown"
        for word in tokenize(text):
            if len(word) < min_word_length or word.isdigit():
                continue
            lang_counters[lang][lemmatize_word(word, morph, lang)] += 1
```

(single-line change: `lemmatize_word(word, morph)` → `lemmatize_word(word, morph, lang)`. `lang` is already computed on the line above and already used to key `lang_counters`, so no new variable is introduced. `_build_descriptions_bow` is untouched — `ImageDescription` rows have no `language` column.)

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_build_ocr_bow_lang_filter.py -v`
Expected: PASS (2 tests: the existing `test_build_ocr_bow_excludes_low_lang_score_rows` and the new one)

- [ ] **Step 5: Commit**

```bash
git add batch/build_bow.py tests/integration/test_build_ocr_bow_lang_filter.py
git commit -m "fix: gate build_bow.py's lemmatization by OCR row language"
```

---

## Task 5: Thread `language` through `ConceptTagger.tag()`

**Files:**
- Modify: `rules/concept_tagger.py:48-53` (the `tag()` method)
- Modify: `tests/rules/test_concept_tagger.py` (append tests)

**Interfaces:**
- Consumes: `normalize(text, morph, min_length, language)` from Task 2
- Produces: `ConceptTagger.tag(self, text: str, language: str | None = None) -> TagResult` — `language` is optional, defaulting to `None` (today's behavior, unchanged for every existing caller)

- [ ] **Step 1: Write the failing tests**

Append to `tests/rules/test_concept_tagger.py`:

```python
# ---------------------------------------------------------------------------
# language parameter (rules/normalize.py language gating)
# ---------------------------------------------------------------------------

def _tags_lang(engine: ConceptTagger, text: str, language: str | None) -> set[str]:
    return {f"{k}:{v}" for k, v in engine.tag(text, language=language).tags}


class TestLanguageParameter:
    def test_no_language_arg_is_backward_compatible(self, tmp_path):
        e = _make_engine(tmp_path, {
            "metallica": {"words": ["metallica"], "votes": {"band:metallica": 1.0}},
        })
        assert "band:metallica" in _tags(e, "Metallica is great")

    def test_language_ru_still_matches_inflected_russian(self, tmp_path):
        e = _make_engine(tmp_path, {
            "work": {"words": ["работа"], "votes": {"тема:работа": 1.0}},
        })
        assert "тема:работа" in _tags_lang(e, "на работе сегодня", "ru")

    def test_language_en_matches_latin_word(self, tmp_path):
        e = _make_engine(tmp_path, {
            "metallica": {"words": ["metallica"], "votes": {"band:metallica": 1.0}},
        })
        assert "band:metallica" in _tags_lang(e, "METALLICA RULES", "en")

    def test_language_unknown_matches_latin_word(self, tmp_path):
        e = _make_engine(tmp_path, {
            "slayer": {"words": ["slayer"], "votes": {"band:slayer": 1.0}},
        })
        assert "band:slayer" in _tags_lang(e, "Slayer tour", "unknown")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/rules/test_concept_tagger.py -v`
Expected: FAIL with `TypeError: tag() got an unexpected keyword argument 'language'` on the three new `_lang` tests (the backward-compat test should already PASS).

- [ ] **Step 3: Implement the fix**

In `rules/concept_tagger.py`, replace the `tag()` method (lines 48-53):

```python
    def tag(self, text: str) -> TagResult:
        lemma_bag = normalize(text, self._morph)
        # Phrases may contain short words ("zz" in "zz top", "in" in "alice in chains").
        # normalize() drops them, making phrase checks impossible. Build a separate full
        # bag with no length filter just for phrase matching.
        lemma_bag_full = normalize(text, self._morph, min_length=1)
```

with:

```python
    def tag(self, text: str, language: str | None = None) -> TagResult:
        lemma_bag = normalize(text, self._morph, language=language)
        # Phrases may contain short words ("zz" in "zz top", "in" in "alice in chains").
        # normalize() drops them, making phrase checks impossible. Build a separate full
        # bag with no length filter just for phrase matching.
        lemma_bag_full = normalize(text, self._morph, min_length=1, language=language)
```

(everything below this point in `tag()` is unchanged — `scores`, `trace`, and the return statement don't reference `language`. No changes to `ConceptTagger.load`, `_load_tags`, or `_load_concepts` — vocabulary loading has no per-word language field to gate on.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/rules/test_concept_tagger.py -v`
Expected: PASS (all tests, including the 4 new ones)

- [ ] **Step 5: Commit**

```bash
git add rules/concept_tagger.py tests/rules/test_concept_tagger.py
git commit -m "feat: add optional language parameter to ConceptTagger.tag()"
```

---

## Task 6: New language-aware repository methods

**Files:**
- Modify: `repository/images.py` (add two new methods; originals untouched)
- Modify: `tests/integration/test_images_repository.py` (append tests)

**Interfaces:**
- Consumes: nothing new
- Produces: `ImagesRepository.get_images_and_ocr_texts_with_language(self)` and `ImagesRepository.get_images_and_ocr_texts_without_tags_with_language(self, source: str)`, both returning rows shaped `(filename, image_id, text, confidence, language, lang_score)` — a 6-tuple, `language` inserted before `lang_score` to match `Storage/models.py::OCRText`'s column order.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_images_repository.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_with_language_includes_language(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(
        image, [(_BBOX, "when your friends finally get the joke", 0.9)], "en"
    )
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_with_language()
    matches = [
        (filename, img_id, txt, confidence, language, lang_score)
        for filename, img_id, txt, confidence, language, lang_score in rows
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == "en"
    assert matches[0][5] == pytest.approx(1.0)


@pytest.mark.asyncio(loop_scope="session")
async def test_get_images_and_ocr_texts_without_tags_with_language_includes_language(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    ocr_repo = OCRTextRepository(db_session)
    await ocr_repo.overwrite_texts(image, [(_BBOX, "ctapt 3gect xdbl qwzk", 0.7)], "en")
    await db_session.flush()

    images_repo = ImagesRepository(db_session)
    rows = await images_repo.get_images_and_ocr_texts_without_tags_with_language("OCR")
    matches = [
        (filename, img_id, txt, confidence, language, lang_score)
        for filename, img_id, txt, confidence, language, lang_score in rows
        if img_id == image.id
    ]

    assert len(matches) == 1
    assert matches[0][4] == "en"
    assert matches[0][5] == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: FAIL with `AttributeError: 'ImagesRepository' object has no attribute 'get_images_and_ocr_texts_with_language'` on the 2 new tests. The 2 existing tests should still PASS (untouched).

- [ ] **Step 3: Implement the new methods**

In `repository/images.py`, add two new methods immediately after `get_images_and_ocr_texts_without_tags` (after line 51, before `get_images_and_descriptions`):

```python
    async def get_images_and_ocr_texts_with_language(self):
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.language,
                self.ocr.lang_score,
            ).join(
                self.ocr, self.ocr.image_id == self.img.id
            )
        )
        result = await self.session.execute(query)
        return result.fetchall()

    async def get_images_and_ocr_texts_without_tags_with_language(self, source: str):
        already_tagged = (
            select(ImageTag.image_id)
            .where(ImageTag.source == source)
            .distinct()
            .scalar_subquery()
        )
        query = (
            select(
                self.img.filename,
                self.img.id,
                self.ocr.text,
                self.ocr.confidence,
                self.ocr.language,
                self.ocr.lang_score,
            )
            .join(self.ocr, self.ocr.image_id == self.img.id)
            .where(self.img.id.not_in(already_tagged))
        )
        result = await self.session.execute(query)
        return result.fetchall()
```

Do not touch `get_images_and_ocr_texts` or `get_images_and_ocr_texts_without_tags` above them — they must remain byte-for-byte identical (see Global Constraints).

- [ ] **Step 4: Run tests to verify they pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_images_repository.py -v`
Expected: PASS (4 tests: 2 existing + 2 new)

- [ ] **Step 5: Confirm the 9 unrelated callers still pass unmodified**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v`
Expected: PASS, including all tests in the file — this confirms the original two methods are untouched and nothing that depends on their 5-tuple shape broke.

- [ ] **Step 6: Commit**

```bash
git add repository/images.py tests/integration/test_images_repository.py
git commit -m "feat: add language-aware ImagesRepository query methods"
```

---

## Task 7: Wire `build_tags_from_ocr.py` to the language-aware path

**Files:**
- Modify: `batch/build_tags_from_ocr.py`

**Interfaces:**
- Consumes: `ImagesRepository.get_images_and_ocr_texts_with_language()` / `..._without_tags_with_language()` from Task 6; `ConceptTagger.tag(text, language)` from Task 5
- Produces: no new public interface — this is a leaf script

No dedicated test file exists for `build_tags_from_ocr.py` today (it's a batch entrypoint, not unit-tested — confirmed by absence of `tests/**/test_build_tags_from_ocr*.py`). Its behavior is already covered by Task 5's `ConceptTagger.tag(language=...)` tests and Task 6's repository tests; this task is pure wiring, verified by an import/syntax check.

- [ ] **Step 1: Make the change**

In `batch/build_tags_from_ocr.py`, replace lines 37-40:

```python
        if incremental:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts_without_tags("OCR")
        else:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts()
```

with:

```python
        if incremental:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts_without_tags_with_language("OCR")
        else:
            images_and_texts_results = await images_repo.get_images_and_ocr_texts_with_language()
```

Then replace lines 49-55 (inside the `async with TagsSaver(session) as tags_saver:` block):

```python
        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text, confidence, lang_score in images_and_texts_results:
                if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
                    metrics.increment("images.skipped")
                    tracker.skip()
                    continue
                result = engine.tag(text)
```

with:

```python
        async with TagsSaver(session) as tags_saver:
            for filename, image_id, text, confidence, language, lang_score in images_and_texts_results:
                if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
                    metrics.increment("images.skipped")
                    tracker.skip()
                    continue
                result = engine.tag(text, language=language or "unknown")
```

(the rest of the loop body — `tag_count`, the `for tag_name, tag_value in result.tags` loop, metrics calls, `tracker.mark_done()` — is unchanged.)

- [ ] **Step 2: Verify the module imports and parses correctly**

Run: `python -c "import batch.build_tags_from_ocr"`
Expected: no output, exit code 0 (no `ImportError`/`SyntaxError`). Run this from the repo root with `.venv311` active, since `Storage.db` requires `DATABASE_URL` to be set — use: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -c "import batch.build_tags_from_ocr"`

- [ ] **Step 3: Commit**

```bash
git add batch/build_tags_from_ocr.py
git commit -m "feat: pass OCR row language into ConceptTagger via the new repository methods"
```

---

## Task 8: `resolve_language()` in `batch/trends/resolution.py`

**Files:**
- Modify: `batch/trends/resolution.py`
- Modify: `tests/batch/test_trends_resolution.py` (append tests)

**Interfaces:**
- Consumes: nothing new
- Produces: `resolve_language(source, settings) -> str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/batch/test_trends_resolution.py`:

```python
from batch.trends.resolution import resolve_language


def test_resolve_language_uses_source_override_when_present():
    source = SimpleNamespace(extraction={"language": "ru"})
    settings = _FakeSettings({"trends.language": "en"})

    assert resolve_language(source, settings) == "ru"


def test_resolve_language_falls_back_to_env_default_when_extraction_is_none():
    source = SimpleNamespace(extraction=None)
    settings = _FakeSettings({"trends.language": "ru"})

    assert resolve_language(source, settings) == "ru"


def test_resolve_language_falls_back_when_extraction_has_no_language_key():
    source = SimpleNamespace(extraction={"model": "some-model"})
    settings = _FakeSettings({"trends.language": "ru"})

    assert resolve_language(source, settings) == "ru"


def test_resolve_language_returns_none_when_nothing_configures_it():
    source = SimpleNamespace(extraction=None)
    settings = _FakeSettings({})

    assert resolve_language(source, settings) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/batch/test_trends_resolution.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_language'`.

- [ ] **Step 3: Implement `resolve_language()`**

In `batch/trends/resolution.py`, add after `resolve_model`:

```python
def resolve_language(source, settings) -> str | None:
    extraction = source.extraction or {}
    language = extraction.get("language")
    if language:
        return language
    return settings.get("trends.language")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/batch/test_trends_resolution.py -v`
Expected: PASS (9 tests: 5 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add batch/trends/resolution.py tests/batch/test_trends_resolution.py
git commit -m "feat: add resolve_language() to trends source resolution"
```

---

## Task 9: Declare Meduza's language in `seed_sources.py`

**Files:**
- Modify: `batch/trends/seed_sources.py`
- Modify: `tests/integration/test_seed_sources.py` (extend one assertion)

**Interfaces:**
- Consumes: nothing new — `TrendSource.extraction` (JSON column) already exists in `Storage/models.py`
- Produces: `MEDUZA_SOURCE["extraction"] == {"language": "ru"}`

- [ ] **Step 1: Write the failing assertion**

In `tests/integration/test_seed_sources.py`, add one line to the existing test (after the `assert matching[0].config["base_url"] == ...` line):

```python
    assert matching[0].extraction == {"language": "ru"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_seed_sources.py -v`
Expected: FAIL — `matching[0].extraction` is currently `None` (the field isn't set on `MEDUZA_SOURCE`).

- [ ] **Step 3: Implement the change**

In `batch/trends/seed_sources.py`, add an `extraction` key to `MEDUZA_SOURCE`:

```python
MEDUZA_SOURCE = {
    "name": "Meduza",
    "connector_type": "api",
    "extraction": {"language": "ru"},
    "config": {
        "base_url": "https://meduza.io/api/w5/new_search",
        "locale": "ru",
        "per_page": 100,
        "num_pages": 100,
        "sleep_every_pages": 10,
        "sleep_seconds": 10,
    },
}
```

(`config.locale` is unrelated and untouched — it's the Meduza API's own content-locale request parameter, not the language signal this plan introduces.)

- [ ] **Step 4: Run test to verify it passes**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_seed_sources.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add batch/trends/seed_sources.py tests/integration/test_seed_sources.py
git commit -m "feat: declare Russian language on the seeded Meduza trend source"
```

---

## Task 10: Wire `trends_batch.py` to lemmatize Russian entities

**Files:**
- Modify: `batch/trends_batch.py`
- Modify: `tests/batch/test_trends_batch.py` (append tests)

**Interfaces:**
- Consumes: `LEMMATIZABLE_LANGUAGES`, `lemmatize_phrase()`, `make_morph()` from Tasks 2/3; `resolve_language()` from Task 8
- Produces: `process_source(source, connector, processor, labels, model_name, language=None, morph=None) -> Counter` — `language`/`morph` are new, both defaulted so the two pre-existing 5-arg calls in `tests/batch/test_trends_batch.py` keep working unmodified.

- [ ] **Step 0: Pre-implementation check — verify GLiNER entity spans are punctuation-clean**

The trends spec's `lemmatize_phrase()` design (implemented in Task 3) assumes GLiNER entity spans don't carry attached punctuation (e.g. a trailing comma with no space) — this was flagged in the spec as an assumption, not yet empirically verified. Check it now, before wiring lemmatization into the real pipeline, using real Meduza data:

Meduza is configured under the `general` environment (`environments/settings.general.yaml` has `trends.labels: ["person", "organization", "location"]` and `trends.model: urchade/gliner_multi-v2.1`; `general` is also the environment with the Russian-language corpus per CLAUDE.md's known gotchas). Use those real values directly:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" python -c "
import string

from config.settings import load_env, settings
load_env('general')

from batch.trends.connectors.api import MeduzaConnector
from batch.trends.processing import Processor

connector = MeduzaConnector('Meduza', {
    'base_url': 'https://meduza.io/api/w5/new_search',
    'locale': 'ru',
    'per_page': 20,
    'num_pages': 1,
})
processor = Processor()
labels = settings.get('trends.labels')
model_name = settings.get('trends.model')

items = connector.fetch()
dirty = []
for item in items[:20]:
    for entity_text, label in processor.process(item['text'], model_name, labels):
        if entity_text != entity_text.strip(string.punctuation + ' '):
            dirty.append((entity_text, label))

print(f'{len(dirty)} entities with attached punctuation out of this sample run')
for e, l in dirty[:10]:
    print(repr(e), l)
"
```

- If the output shows 0 (or a negligible number of) dirty entities: the assumption holds, proceed to Step 1 with `lemmatize_phrase()` unchanged.
- If a meaningful number of entities carry attached punctuation: go back to Task 3 and change `lemmatize_phrase()` in `rules/normalize.py` to strip punctuation per chunk before lemmatizing:

  ```python
  import string

  def lemmatize_phrase(text: str, morph: pymorphy3.MorphAnalyzer) -> str:
      """Lemmatize each whitespace-delimited chunk of text, preserving
      internal punctuation (e.g. hyphens in compound names) and word order."""
      return " ".join(
          lemmatize_word(chunk.strip(string.punctuation), morph)
          for chunk in text.split()
      )
  ```

  Add a regression test to `tests/rules/test_normalize.py` (`TestLemmatizePhrase`) covering the specific punctuation pattern found, then re-run `pytest tests/rules/test_normalize.py -v` before continuing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/batch/test_trends_batch.py` (add `from rules.normalize import make_morph` to the imports at the top):

```python
from rules.normalize import make_morph


def test_process_source_merges_inflected_russian_entities_when_language_ru():
    source = SimpleNamespace(name="Meduza")
    connector = _FakeConnector([
        {"title": "A", "text": "Путина заявление"},
        {"title": "B", "text": "Путин выступил"},
    ])
    processor = _FakeProcessor({
        "Путина заявление": [("Путина", "person")],
        "Путин выступил": [("Путин", "person")],
    })
    morph = make_morph()

    trends = process_source(source, connector, processor, ["person"], "model-a", "ru", morph)

    assert trends == Counter({"person:путин": 2})


def test_process_source_leaves_entity_text_untouched_when_language_not_lemmatizable():
    source = SimpleNamespace(name="LoudWire")
    connector = _FakeConnector([
        {"title": "A", "text": "Путина заявление"},
        {"title": "B", "text": "Путин выступил"},
    ])
    processor = _FakeProcessor({
        "Путина заявление": [("Путина", "person")],
        "Путин выступил": [("Путин", "person")],
    })

    trends = process_source(source, connector, processor, ["person"], "model-a", None, None)

    assert trends == Counter({"person:Путина": 1, "person:Путин": 1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/batch/test_trends_batch.py -v`
Expected: FAIL with `TypeError: process_source() takes from 5 to 5 positional arguments but 7 were given` on the two new tests. The two existing tests should still PASS unmodified.

- [ ] **Step 3: Implement `process_source()` and `main()` wiring**

In `batch/trends_batch.py`, replace the imports at the top:

```python
import argparse
import asyncio
from collections import Counter

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from batch.trends.connectors.registry import get_connector
from batch.trends.processing import Processor
from batch.trends.resolution import resolve_labels, resolve_model
from repository.trends import TrendSourceRepository, TrendsRunRepository, TrendsRunResultRepository
```

with:

```python
import argparse
import asyncio
from collections import Counter

import pymorphy3

from config.settings import load_env, settings
from Storage.db import AsyncSessionLocal
from batch.trends.connectors.registry import get_connector
from batch.trends.processing import Processor
from batch.trends.resolution import resolve_labels, resolve_language, resolve_model
from repository.trends import TrendSourceRepository, TrendsRunRepository, TrendsRunResultRepository
from rules.normalize import LEMMATIZABLE_LANGUAGES, lemmatize_phrase, make_morph
```

Replace `process_source()`:

```python
def process_source(source, connector, processor: Processor, labels: list[str], model_name: str) -> Counter:
    trends = Counter()
    data = connector.fetch()
    print(f"Scraping {source.name}")
    for item in data:
        print("\n---")
        title = item["title"]
        print(title)
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            trends[f"{label}:{entity_text}"] += 1
    return trends
```

with:

```python
def process_source(source, connector, processor: Processor, labels: list[str], model_name: str,
                    language: str | None = None, morph: pymorphy3.MorphAnalyzer | None = None) -> Counter:
    trends = Counter()
    data = connector.fetch()
    print(f"Scraping {source.name}")
    for item in data:
        print("\n---")
        title = item["title"]
        print(title)
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            if language in LEMMATIZABLE_LANGUAGES:
                entity_text = lemmatize_phrase(entity_text, morph)
            trends[f"{label}:{entity_text}"] += 1
    return trends
```

Replace `main()`:

```python
async def main():
    processor = Processor()

    async with AsyncSessionLocal() as session:

        sources_repo = TrendSourceRepository(session)
        sources = await sources_repo.get_all()

        runs_repo = TrendsRunRepository(session)

        run_id = await runs_repo.create_run()

        results_repo = TrendsRunResultRepository(session, run_id)

        try:
            for source in sources:
                connector = get_connector(source.name, source.connector_type, source.config)
                labels = resolve_labels(source, settings)
                model_name = resolve_model(source, settings)

                trends = process_source(source, connector, processor, labels, model_name)

                for topic, value in trends.items():
                    label, name = topic.split(":", 1)
                    await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)

            await runs_repo.commit(run_id)
        except Exception:
            await runs_repo.fail(run_id)
            raise
        finally:
            await session.commit()
```

with:

```python
async def main():
    processor = Processor()
    morph = make_morph()

    async with AsyncSessionLocal() as session:

        sources_repo = TrendSourceRepository(session)
        sources = await sources_repo.get_all()

        runs_repo = TrendsRunRepository(session)

        run_id = await runs_repo.create_run()

        results_repo = TrendsRunResultRepository(session, run_id)

        try:
            for source in sources:
                connector = get_connector(source.name, source.connector_type, source.config)
                labels = resolve_labels(source, settings)
                model_name = resolve_model(source, settings)
                language = resolve_language(source, settings)

                trends = process_source(source, connector, processor, labels, model_name, language, morph)

                for topic, value in trends.items():
                    label, name = topic.split(":", 1)
                    await results_repo.add_result(source_id=source.id, label=label, name=name, value=value)

            await runs_repo.commit(run_id)
        except Exception:
            await runs_repo.fail(run_id)
            raise
        finally:
            await session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/batch/test_trends_batch.py -v`
Expected: PASS (4 tests: 2 existing unmodified + 2 new)

- [ ] **Step 5: Run the full no-DB suite to check for regressions**

Run: `pytest tests/rules/ tests/batch/ -v`
Expected: PASS (all tests across both roots — this exercises everything built in Tasks 1-3, 5, 8, and 10 together)

- [ ] **Step 6: Commit**

```bash
git add batch/trends_batch.py tests/batch/test_trends_batch.py
git commit -m "feat: lemmatize Russian trend entities before counting"
```

---

## Final Verification

- [ ] Run the full no-DB suite: `pytest tests/rules/ tests/batch/ -v` — expect all PASS.
- [ ] Run the full DB-backed suite: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v` — expect all PASS.
- [ ] Confirm the three source specs' requirements are all covered: `docs/superpowers/specs/2026-07-17-ocr-tokenize-punctuation-preservation-design.md` (Task 1), `docs/superpowers/specs/2026-07-17-ocr-lemmatization-language-gating-design.md` (Tasks 2, 4-7), `docs/superpowers/specs/2026-07-17-trends-lemmatization-design.md` (Tasks 3, 8-10).
- [ ] Do **not** run `python -m batch.build_bow`, `python -m batch.build_tags_from_ocr`, or `python -m batch.trends_batch` against any real environment as part of this work — flag to the user that a full rebuild (per the language-gating spec's Implementation Order step 7) is a manual follow-up they should run themselves when ready, since it reprocesses real production data.
