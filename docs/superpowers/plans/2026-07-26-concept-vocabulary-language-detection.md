# Concept Vocabulary Language Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the vocabulary/text stemming asymmetry the English-lemmatization feature's final review found — hand-curated concept/tag/ignore-word vocabulary should get the same per-word Russian-lemmatization or English-stemming treatment as the OCR text it's matched against, instead of only ever being plain-lowercased.

**Architecture:** A new `lemmatize_word_autodetect(word, morph)` function in `rules/normalize.py` detects each vocabulary word's own script (reusing the already-existing `is_cyrillic_word`/`is_latin_word` checks built for the phonetic-erratives and English-stemming features) and dispatches to real Russian lemmatization, English stemming, or the unchanged plain-lowercase fallback accordingly — no new data/metadata needed, since individual vocabulary words are already almost always pure single-script tokens even when a word *list* mixes languages.

**Tech Stack:** Python 3.11, pytest (no DB, no I/O — every file this plan touches is pure Python logic over already-loaded strings).

## Global Constraints

- Exactly six call sites change, all from `lemmatize_word(word, morph)` (no `language` argument) to `lemmatize_word_autodetect(word, morph)`: two in `rules/concept_tagger.py::_load_concepts`, one in `batch/build_bow.py::_load_ignore_lemmas`, one in `batch/build_bow.py::_build_json_rules_lemma_set`, two in `batch/build_bow.py::_build_concepts_lemma_set`. No other call site changes — in particular, `ConceptTagger.tag()`'s OCR-text path, `build_bow.py::_build_ocr_bow`, and `build_bow.py::_build_descriptions_bow` are explicitly out of scope and must not be touched.
- `lemmatize_word_autodetect` must lowercase the word before checking its script (`is_cyrillic_word`/`is_latin_word` both expect already-lowercased input), then pass the *original* `word` (not the lowercased copy) to `lemmatize_word` — `lemmatize_word` and `stem_english_word` already handle their own casing internally.
- No new gate or safety check beyond what's already in the design — `concept_tagger.py`'s existing homonym-detection warning is the accepted safety net for this change; do not add a new one.
- No schema, settings, or migration changes anywhere in this plan.

---

## File Structure

- Modify `rules/normalize.py` — new `lemmatize_word_autodetect` function + two new imports (Task 1).
- Modify `tests/rules/test_normalize.py` — unit tests for the new function (Task 1).
- Modify `rules/concept_tagger.py` — 2 call sites in `_load_concepts` (Task 2).
- Modify `tests/rules/test_concept_tagger.py` — vocabulary/text symmetry test (Task 2).
- Modify `batch/build_bow.py` — 3 functions, 4 call sites total (Task 3).
- Create `batch/tests/test_build_bow_vocab.py` — unit tests for those 3 functions (Task 3).

---

### Task 1: `lemmatize_word_autodetect`

**Files:**
- Modify: `rules/normalize.py` (imports at lines 1-5; new function goes directly after `lemmatize_word`, which currently ends at line 86)
- Test: `tests/rules/test_normalize.py`

**Interfaces:**
- Produces: `rules.normalize.lemmatize_word_autodetect(word: str, morph: pymorphy3.MorphAnalyzer) -> str`. Tasks 2 and 3 depend on this existing and being imported from `rules.normalize`.

- [ ] **Step 1: Write the failing tests**

In `tests/rules/test_normalize.py`, add a new test class after `TestLemmatizeWordUnknownWordsStayAsTyped` (the last class in the `lemmatize_word`-focused section of the file, directly before the `from rules.normalize import lemmatize_phrase` import further down):

```python
class TestLemmatizeWordAutodetect:
    def test_cyrillic_word_gets_real_lemmatization(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("кошки", morph) == "кошка"

    def test_latin_word_gets_stemmed(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("cats", morph) == "cat"

    def test_digit_only_word_falls_through_to_plain_lowercase(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("2020", morph) == "2020"

    def test_mixed_script_word_falls_through_to_plain_lowercase(self):
        morph = make_morph()
        assert lemmatize_word_autodetect("METALLICAкринж", morph) == "metallicaкринж"

    def test_uppercase_latin_word_is_detected_and_stemmed(self):
        # is_cyrillic_word/is_latin_word require already-lowercased input;
        # this pins that lemmatize_word_autodetect lowercases before checking.
        morph = make_morph()
        assert lemmatize_word_autodetect("CATS", morph) == "cat"
```

Add `lemmatize_word_autodetect` to the existing `from rules.normalize import lemmatize_word, make_morph, normalize` import line (the one at line 56 of the current file) so it reads:

```python
from rules.normalize import lemmatize_word, lemmatize_word_autodetect, make_morph, normalize
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_normalize.py -k TestLemmatizeWordAutodetect -v`
Expected: FAIL / collection error — `ImportError: cannot import name 'lemmatize_word_autodetect' from 'rules.normalize'`.

- [ ] **Step 3: Implement `lemmatize_word_autodetect`**

In `rules/normalize.py`, the current imports (lines 1-5) read:

```python
import re

import pymorphy3

from rules.english_stemming import stem_english_word
```

Change to:

```python
import re

import pymorphy3

from rules.english_stemming import is_latin_word, stem_english_word
from rules.phonetic import is_cyrillic_word
```

Directly after `lemmatize_word`'s closing `return parsed[0].normal_form` (the function currently ends at line 86, followed by two blank lines and then `_TOKEN_RE = re.compile(...)`), add:

```python
def lemmatize_word_autodetect(word: str, morph: pymorphy3.MorphAnalyzer) -> str:
    """
    Like lemmatize_word, but for callers with no external language signal
    at all (hand-curated vocabulary in concepts/tags/ignore-word files,
    as opposed to OCR text rows which carry their own detected language).
    Detects the word's own script and dispatches accordingly, reusing
    is_cyrillic_word/is_latin_word (the same checks matching_image_ids
    uses at query time) instead of requiring per-word language metadata
    in the data files -- vocabulary words are already almost always pure
    single-script tokens ("opeth", "кринж"), even when a word list mixes
    languages overall. See
    docs/superpowers/specs/2026-07-26-concept-vocabulary-language-detection-design.md.
    """
    lowered = word.lower()
    if is_cyrillic_word(lowered):
        return lemmatize_word(word, morph, language="ru")
    if is_latin_word(lowered):
        return lemmatize_word(word, morph, language="en")
    return lemmatize_word(word, morph)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_normalize.py -v`
Expected: PASS, every test in the file green (both the new class and every pre-existing test — confirming no regression to any existing `lemmatize_word`/`normalize` behavior).

- [ ] **Step 5: Commit**

```bash
git add rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: add lemmatize_word_autodetect for hand-curated vocabulary loading"
```

---

### Task 2: `rules/concept_tagger.py` vocabulary loading

**Files:**
- Modify: `rules/concept_tagger.py` (`_load_concepts`, currently lines 129-138)
- Modify: `tests/rules/test_concept_tagger.py`

**Interfaces:**
- Consumes: `rules.normalize.lemmatize_word_autodetect` (Task 1).

- [ ] **Step 1: Write the failing test**

In `tests/rules/test_concept_tagger.py`, add a new test class after the existing single-word matching tests (find the `class TestSingleWord:` block and add this new class directly after it closes, before the next `class` in the file). Use the file's existing `_make_engine` helper exactly as-is:

```python
class TestVocabularyLanguageAutodetection:
    def test_english_vocabulary_word_matches_stemmed_ocr_text(self, tmp_path):
        """The vocabulary word "cat" is itself already a stem, so this
        only proves the match works when OCR text is tagged "en" (and
        therefore stemmed at query/tag time) -- it does NOT yet prove the
        vocabulary side is being autodetected/stemmed. See the next test
        for that."""
        e = _make_engine(tmp_path, {
            "felines": {"words": ["cat"], "votes": {"topic:cat": 1.0}},
        })

        result = e.tag("look at these cats", language="en")

        assert ("topic", "cat") in result.tags

    def test_inflected_english_vocabulary_word_matches_via_stemming(self, tmp_path):
        """The vocabulary word "cats" (inflected, not a base form) only
        matches OCR text "cat" if the vocabulary side is ALSO stemmed at
        load time -- this is the actual vocab/text symmetry this feature
        adds. Before this feature, "cats" would have been loaded as the
        plain lowercase string "cats", which would never match a stemmed
        "cat" in OCR text."""
        e = _make_engine(tmp_path, {
            "felines": {"words": ["cats"], "votes": {"topic:cat": 1.0}},
        })

        result = e.tag("I have one cat", language="en")

        assert ("topic", "cat") in result.tags
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_concept_tagger.py -k TestVocabularyLanguageAutodetection -v`
Expected: `test_english_vocabulary_word_matches_stemmed_ocr_text` FAILS (vocabulary word `"cat"` is currently loaded via plain `lemmatize_word(p, morph)` with `language=None`, which lowercases but does not stem — this happens to still equal `"cat"` for this exact word, so this test's failure here would actually indicate something is already broken elsewhere; if it unexpectedly passes already, that's fine, move on and let Step 4 confirm the *other* test). `test_inflected_english_vocabulary_word_matches_via_stemming` FAILS — vocabulary word `"cats"` stays `"cats"` under current unstemmed loading, and OCR text `"cat"` stems to `"cat"` (already a stem) or stays `"cat"`, so `"cats"` (vocab) never equals `"cat"` (text) without the fix.

- [ ] **Step 3: Update `_load_concepts`**

In `rules/concept_tagger.py`, the current imports read:

```python
from rules.normalize import lemmatize_word, make_morph, normalize
```

Change to:

```python
from rules.normalize import lemmatize_word_autodetect, make_morph, normalize
```

The current `_load_concepts` body has this section (currently lines 129-138):

```python
        # Lemmatize every word entry; multi-word phrases become space-joined lemma strings
        lemmatized: set[str] = set()
        for w in raw_words:
            parts = w.split()
            lemmatized.add(" ".join(lemmatize_word(p, morph) for p in parts))

        fuzzy = [
            {"lemma": lemmatize_word(fe["word"], morph), "threshold": fe["threshold"]}
            for fe in raw_fuzzy
        ]
```

Change to:

```python
        # Lemmatize every word entry; multi-word phrases become space-joined lemma strings
        lemmatized: set[str] = set()
        for w in raw_words:
            parts = w.split()
            lemmatized.add(" ".join(lemmatize_word_autodetect(p, morph) for p in parts))

        fuzzy = [
            {"lemma": lemmatize_word_autodetect(fe["word"], morph), "threshold": fe["threshold"]}
            for fe in raw_fuzzy
        ]
```

(`lemmatize_word` itself is no longer referenced anywhere in this file after this change — the import replacement above already drops it, since `lemmatize_word_autodetect` is the only lemmatization entry point `concept_tagger.py` needs at load time; `normalize()`, used elsewhere in this same file for OCR-text tagging, calls `lemmatize_word` internally on its own and doesn't need it imported here separately.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest tests/rules/test_concept_tagger.py -v`
Expected: PASS, every test in the file green (both new tests and every pre-existing test — confirming no regression to existing concept-tagging behavior, including Russian-vocabulary concepts already in the test suite).

- [ ] **Step 5: Commit**

```bash
git add rules/concept_tagger.py tests/rules/test_concept_tagger.py
git commit -m "feat: autodetect language when loading concept vocabulary"
```

---

### Task 3: `batch/build_bow.py` vocabulary loading

**Files:**
- Modify: `batch/build_bow.py` (`_load_ignore_lemmas` at line 47-50, `_build_json_rules_lemma_set` at line 60-68, `_build_concepts_lemma_set` at line 71-81)
- Create: `batch/tests/test_build_bow_vocab.py`

**Interfaces:**
- Consumes: `rules.normalize.lemmatize_word_autodetect` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `batch/tests/test_build_bow_vocab.py`:

```python
"""
Unit tests for batch/build_bow.py's vocabulary-loading functions
(_load_ignore_lemmas, _build_json_rules_lemma_set,
_build_concepts_lemma_set). No DB, no I/O beyond reading fixture files
this test itself writes to tmp_path.
"""
import json

import yaml

from batch.build_bow import (
    _build_concepts_lemma_set,
    _build_json_rules_lemma_set,
    _load_ignore_lemmas,
)
from rules.normalize import make_morph


class TestLoadIgnoreLemmas:
    def test_english_word_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "ignore.json"
        path.write_text(json.dumps(["cats"]), encoding="utf-8")

        result = _load_ignore_lemmas(morph, path)

        assert result == {"cat"}

    def test_russian_word_is_lemmatized(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "ignore.json"
        path.write_text(json.dumps(["кошки"]), encoding="utf-8")

        result = _load_ignore_lemmas(morph, path)

        assert result == {"кошка"}


class TestBuildJsonRulesLemmaSet:
    def test_english_word_in_rule_key_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "rules.json"
        path.write_text(json.dumps({"cats": {"topic": "cat"}}), encoding="utf-8")

        result = _build_json_rules_lemma_set(morph, path)

        assert result == {"cat"}


class TestBuildConceptsLemmaSet:
    def test_english_word_entry_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "concepts.yaml"
        path.write_text(
            yaml.dump({"felines": {"words": ["cats"], "votes": {"topic:cat": 1.0}}}),
            encoding="utf-8",
        )

        result = _build_concepts_lemma_set(morph, path)

        assert result == {"cat"}

    def test_english_fuzzy_entry_is_stemmed(self, tmp_path):
        morph = make_morph()
        path = tmp_path / "concepts.yaml"
        path.write_text(
            yaml.dump({
                "felines": {
                    "words": [],
                    "fuzzy": [{"word": "cats", "threshold": 85}],
                    "votes": {"topic:cat": 1.0},
                }
            }),
            encoding="utf-8",
        )

        result = _build_concepts_lemma_set(morph, path)

        assert result == {"cat"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest batch/tests/test_build_bow_vocab.py -v`
Expected: FAIL — every test asserts a stemmed result (`"cat"`) but the current, unmodified functions produce the plain-lowercased, unstemmed string (`"cats"`) instead.

- [ ] **Step 3: Update the three functions**

In `batch/build_bow.py`, the current imports read:

```python
from rules.normalize import lemmatize_word, make_morph, tokenize
```

Change to:

```python
from rules.normalize import lemmatize_word_autodetect, make_morph, tokenize
```

The current `_load_ignore_lemmas` reads:

```python
def _load_ignore_lemmas(morph, path):
    with open(path, encoding="utf-8") as f:
        words = json.load(f)
    return {lemmatize_word(w, morph) for w in words}
```

Change to:

```python
def _load_ignore_lemmas(morph, path):
    with open(path, encoding="utf-8") as f:
        words = json.load(f)
    return {lemmatize_word_autodetect(w, morph) for w in words}
```

The current `_build_json_rules_lemma_set` reads:

```python
def _build_json_rules_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_thresholds", None)
    covered = set()
    for rule_key in data:
        for word in tokenize(rule_key):
            covered.add(lemmatize_word(word, morph))
    return covered
```

Change to:

```python
def _build_json_rules_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data.pop("_thresholds", None)
    covered = set()
    for rule_key in data:
        for word in tokenize(rule_key):
            covered.add(lemmatize_word_autodetect(word, morph))
    return covered
```

The current `_build_concepts_lemma_set` reads:

```python
def _build_concepts_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    covered = set()
    for _concept_name, cfg in (data or {}).items():
        for word in (cfg.get("words") or []):
            for token in tokenize(word):
                covered.add(lemmatize_word(token, morph))
        for fe in (cfg.get("fuzzy") or []):
            covered.add(lemmatize_word(fe["word"], morph))
    return covered
```

Change to:

```python
def _build_concepts_lemma_set(morph, path):
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    covered = set()
    for _concept_name, cfg in (data or {}).items():
        for word in (cfg.get("words") or []):
            for token in tokenize(word):
                covered.add(lemmatize_word_autodetect(token, morph))
        for fe in (cfg.get("fuzzy") or []):
            covered.add(lemmatize_word_autodetect(fe["word"], morph))
    return covered
```

Do **not** change `_build_ocr_bow` (still uses `lemmatize_word(word, morph, lang)` with the row's real language — unaffected, out of scope) or `_build_descriptions_bow` (still uses plain `lemmatize_word(word, morph)` with no language — unaffected, out of scope per this plan's Global Constraints).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest batch/tests/test_build_bow_vocab.py -v`
Expected: PASS, all tests green.

Also run the full `batch/tests/` suite to confirm no regression to anything else in that root:

Run: `cd H:\workspace_sandbox\memes && .venv311\Scripts\pytest batch/tests/ -v`
Expected: PASS, every test in the directory green.

- [ ] **Step 5: Commit**

```bash
git add batch/build_bow.py batch/tests/test_build_bow_vocab.py
git commit -m "feat: autodetect language when loading build_bow vocabulary sets"
```

---

## After all tasks: operational note for the final whole-branch review

Confirm (by reading, not running): `ConceptTagger.tag()`'s OCR-text path, `build_bow.py::_build_ocr_bow`, and `build_bow.py::_build_descriptions_bow` are untouched in the diff — these were deliberately out of scope per the design. This branch makes no schema/database changes, and — per the design doc's operational note — does not itself require any batch-job rerun to merge safely; `build_tags_from_ocr`/`build_bow` reruns in `metal`/`general`/`it` are a separate, later, explicit-go-ahead decision, not part of this branch's work.
