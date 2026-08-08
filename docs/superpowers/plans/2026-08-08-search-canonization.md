# Search Canonization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three narrow, fixed-rule text canonizations (Cyrillic ё→е, British/American spelling variants, negative-contraction expansion) to `rules/normalize.py`, so equivalent forms match across the rules engine, `build_bow.py`, and search.

**Architecture:** Single task. A new data-only module (`rules/canonical_forms.py`, two fixed dicts) plus three small, tightly-coupled edits to the one file this feature is entirely about (`rules/normalize.py`) — no natural second reviewable unit to split into.

**Tech Stack:** Python 3.11, no new dependencies. `tests/rules/` (existing dedicated unit-test root for this module — no DB, no I/O).

## Post-implementation note (2026-08-08)

This plan's steps were executed as written (including the ё→е canonicalization steps below), but
the final whole-branch review found that piece was unnecessary and actively harmful — see
`docs/superpowers/specs/2026-08-08-search-canonization-design.md`'s "Investigated and rejected:
Cyrillic ё→е" section for the full finding (pymorphy3 already restores ё for known words, so the
tokenize-time fold did nothing for its motivating case, while regressing 15 concept-vocabulary
entries and 8 tag values in `general`). The ё/Ё entries and their two test classes
(`TestTokenizeCyrillicYoNormalization`, `TestNormalizeCyrillicYoEquivalence`) were removed after
the steps below were completed, as part of closing out the final review. The step-by-step history
below is left as originally written for an accurate record of what was actually done at each
point, rather than rewritten to pretend ё was never attempted.

## Global Constraints

- Fixed, curated word lists only — no general suffix-transformation rule (rejected in the spec due to false-positive collision risk, e.g. a blanket `-ise`→`-ize` rule would incorrectly conflate "prise"/"prize", two distinct words).
- Only negative ("n't") contractions are covered — not "it's"/"that's"/"let's"/"I'm" (semantically ambiguous, don't reduce as cleanly).
- Spelling-variant canonicalization goes inside `lemmatize_word()` (reaches every caller, including `build_bow.py` which calls it directly). Contraction expansion goes inside `normalize()`'s loop (produces multiple lemmas per token, so it can't fit `lemmatize_word()`'s one-in-one-out contract — this means it does *not* reach `build_bow.py`, which calls `tokenize()`/`lemmatize_word()` directly rather than `normalize()`; this asymmetry is intentional, not a bug to fix here).
- No new fallback tier in `repository/ocr_lemmas.py`, no DB schema change — everything happens at normalization time, matched via the existing exact-lemma tier.
- No `CLAUDE.md`/runbook documentation changes needed (no new script, no CLI/config surface change, no API endpoint).

---

### Task 1: ё→е, spelling variants, and contraction expansion

**Files:**
- Create: `rules/canonical_forms.py`
- Modify: `rules/normalize.py` (the `_CHAR_NORMALIZE` rename + ё/Ё entries; the `SPELLING_VARIANTS` lookup inside `lemmatize_word()`; the `CONTRACTION_EXPANSIONS` handling inside `normalize()`'s loop)
- Test: `tests/rules/test_normalize.py`

**Interfaces:**
- Produces: `rules.canonical_forms.SPELLING_VARIANTS: dict[str, str]` (British form → American form, lowercase keys/values), `rules.canonical_forms.CONTRACTION_EXPANSIONS: dict[str, list[str]]` (apostrophe-stripped lowercase contraction → list of full words it expands to). Neither is consumed by any other task — this is the only task in this plan.
- Consumes: nothing new — `lemmatize_word()` and `normalize()`'s existing signatures are unchanged (no new parameters), only their internal bodies change.

This task has no meaningful TDD "implement to pass one failing test" cycle for the two lookup
tables themselves (they're plain data, not logic) — the steps below write the data module first,
then the three integration edits with their own failing/passing test cycle each, since those are
where actual behavior changes.

- [x] **Step 1: Create `rules/canonical_forms.py`**

```python
"""
Fixed, curated equivalence tables for text canonization -- narrow, hand-maintained lists
rather than a general phonetic/fuzzy system. See
docs/superpowers/specs/2026-08-08-search-canonization-design.md.
"""

# British -> American spelling. Only the British form needs a key; the American form already
# passes through unchanged (it's what index/query text canonicalizes toward). Covers only
# base/dictionary forms -- inflected forms not listed here (e.g. "categorising") are not
# specially handled; the existing Snowball stemmer may separately unify some of them once
# canonicalized, but this isn't guaranteed. Deliberately a fixed list, not a suffix rule --
# see design doc's Out-of-scope section for why (the "prise"/"prize" collision risk).
SPELLING_VARIANTS: dict[str, str] = {
    # -ise/-ize (and -yse/-yze) verbs
    "realise": "realize", "organise": "organize", "recognise": "recognize",
    "categorise": "categorize", "initialise": "initialize", "customise": "customize",
    "analyse": "analyze", "paralyse": "paralyze", "finalise": "finalize",
    "characterise": "characterize", "apologise": "apologize", "criticise": "criticize",
    "emphasise": "emphasize", "memorise": "memorize", "minimise": "minimize",
    "maximise": "maximize", "optimise": "optimize", "summarise": "summarize",
    "standardise": "standardize", "specialise": "specialize", "familiarise": "familiarize",
    "prioritise": "prioritize", "capitalise": "capitalize", "symbolise": "symbolize",
    "sympathise": "sympathize", "utilise": "utilize",
    # -isation/-ization nouns
    "realisation": "realization", "organisation": "organization",
    "categorisation": "categorization", "initialisation": "initialization",
    "customisation": "customization", "optimisation": "optimization",
    "standardisation": "standardization", "specialisation": "specialization",
    "minimisation": "minimization", "maximisation": "maximization",
    "summarisation": "summarization", "prioritisation": "prioritization",
    "capitalisation": "capitalization", "utilisation": "utilization",
    # -our/-or
    "colour": "color", "favour": "favor", "favourite": "favorite", "humour": "humor",
    "flavour": "flavor", "honour": "honor", "neighbour": "neighbor",
    "behaviour": "behavior", "colourful": "colorful",
    # -re/-er
    "centre": "center", "theatre": "theater", "litre": "liter", "fibre": "fiber",
    "metre": "meter",
}

# Negative ("n't") contractions -> their expansion, keyed on the apostrophe-stripped lowercase
# form so both "don't" and "dont" (OCR frequently drops apostrophes) hit the same entry.
# Each value is lemmatized word-by-word and added to the result set like any other word,
# subject to the caller's own min_length filter -- so e.g. "don't"/"dont" both contribute
# {"not"} (the same lemma set literal "do not" text already produces today, since "do" is
# below the default min_length), closing the equivalence via the existing AND-of-lemmas
# matching with no new machinery. Only "n't" forms are covered -- other contractions ("it's",
# "that's", "let's", "I'm") are semantically ambiguous ("it's" = "it is" or "it has"?) and
# don't reduce as cleanly; out of scope for this narrow pass.
CONTRACTION_EXPANSIONS: dict[str, list[str]] = {
    "dont": ["do", "not"],
    "cant": ["can", "not"],
    "wont": ["will", "not"],
    "isnt": ["is", "not"],
    "arent": ["are", "not"],
    "wasnt": ["was", "not"],
    "werent": ["were", "not"],
    "doesnt": ["does", "not"],
    "didnt": ["did", "not"],
    "hasnt": ["has", "not"],
    "havent": ["have", "not"],
    "hadnt": ["had", "not"],
    "wouldnt": ["would", "not"],
    "couldnt": ["could", "not"],
    "shouldnt": ["should", "not"],
}
```

- [x] **Step 2: Confirm the module imports cleanly**

Run: `python -c "from rules.canonical_forms import SPELLING_VARIANTS, CONTRACTION_EXPANSIONS; print(len(SPELLING_VARIANTS), len(CONTRACTION_EXPANSIONS))"`
Expected: prints `54 15` (no import errors) — 54 entries in `SPELLING_VARIANTS` (26 `-ise/-ize` verbs + 14 `-isation/-ization` nouns + 9 `-our/-or` + 5 `-re/-er`), 15 entries in `CONTRACTION_EXPANSIONS`.

- [x] **Step 3: Write the failing tests for ё→е**

Add to `tests/rules/test_normalize.py`, inside (or near) the existing `TestTokenizeJoinerNormalization` class (which already tests the same `_CHAR_NORMALIZE`/`_normalize_chars` mechanism for dashes/quotes — this is the same class of test, just a new mapping in the same table):

```python
class TestTokenizeCyrillicYoNormalization:
    def test_lowercase_yo_normalized_to_ye(self):
        assert tokenize("всё") == ["все"]

    def test_uppercase_yo_normalized_to_ye(self):
        assert tokenize("Ёж") == ["Еж"]
```

And, further down near `TestNormalizeLanguageGating` (which already calls `normalize()` end to end):

```python
class TestNormalizeCyrillicYoEquivalence:
    def test_yo_and_ye_spellings_produce_overlapping_lemmas(self):
        morph = make_morph()
        assert normalize("всё", morph) & normalize("все", morph)
```

- [x] **Step 4: Run the tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -k CyrillicYo -v`
Expected: FAIL — `tokenize("всё")` currently returns `["всё"]` (unchanged ё), not `["все"]`.

- [x] **Step 5: Rename `_JOINER_NORMALIZE`/`_normalize_joiners` and add the ё/Ё mapping**

In `rules/normalize.py`, replace:

```python
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

with:

```python
_CHAR_NORMALIZE = str.maketrans({
    "–": "-",   # en dash
    "—": "-",   # em dash
    "’": "'",   # right single quotation mark / smart apostrophe
    "ё": "е",   # Cyrillic ё -> е -- casual typing overwhelmingly substitutes е for ё; not
    "Ё": "Е",   # already covered by the phonetic-erratives fallback, which only fires for
                # words pymorphy3 doesn't recognize (see rules/phonetic.py, and
                # docs/superpowers/specs/2026-08-08-search-canonization-design.md's Motivation)
})


def _normalize_chars(text: str) -> str:
    return text.translate(_CHAR_NORMALIZE)


def tokenize(text: str) -> list[str]:
    # [^\W_] = letters and digits only; underscores treated as delimiters so that
    # social-media handles like "varg_vikernes" split into ["varg", "vikernes"].
    # A single '-' or "'" between two word-character runs stays part of the token
    # (compounds like "Санкт-Петербурга", contractions like "don't"); every other
    # occurrence of either character — with no word character immediately
    # following — still splits/strips as before. Em/en dashes, the curly
    # apostrophe, and Cyrillic ё are normalized to their canonical counterparts
    # first so there's one canonical form per character.
    return _TOKEN_RE.findall(_normalize_chars(text))
```

- [x] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -k CyrillicYo -v`
Expected: PASS (3 passed)

- [x] **Step 7: Write the failing tests for spelling variants**

Add to `tests/rules/test_normalize.py`, near `TestLemmatizeWordStemmable` (which already tests the `STEMMABLE_LANGUAGES` path this needs to cover too):

```python
class TestLemmatizeWordSpellingVariants:
    def test_ise_ize_verb_pair_matches(self):
        morph = make_morph()
        assert lemmatize_word("categorise", morph) == lemmatize_word("categorize", morph)

    def test_isation_ization_noun_pair_matches(self):
        morph = make_morph()
        assert lemmatize_word("initialisation", morph) == lemmatize_word("initialization", morph)

    def test_our_or_pair_matches(self):
        morph = make_morph()
        assert lemmatize_word("colour", morph) == lemmatize_word("color", morph)

    def test_re_er_pair_matches(self):
        morph = make_morph()
        assert lemmatize_word("centre", morph) == lemmatize_word("center", morph)

    def test_unlisted_word_unaffected(self):
        morph = make_morph()
        assert lemmatize_word("surprise", morph, language="en") == "surpris"

    def test_stemmable_language_path_also_canonicalizes(self):
        morph = make_morph()
        assert (
            lemmatize_word("categorise", morph, language="en")
            == lemmatize_word("categorize", morph, language="en")
        )
```

Note on `test_unlisted_word_unaffected`: the expected value `"surpris"` was confirmed directly
against the real Snowball stemmer (`stem_english_word("surprise")`) while writing this plan, not
guessed. The point of the test is that "surprise" is *not* in `SPELLING_VARIANTS` and is therefore
unaffected by this change — a regression guard, not something this task is expected to change.

- [x] **Step 8: Run the tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -k SpellingVariants -v`
Expected: `test_ise_ize_verb_pair_matches`, `test_isation_ization_noun_pair_matches`,
`test_our_or_pair_matches`, `test_re_er_pair_matches`, and
`test_stemmable_language_path_also_canonicalizes` FAIL (the two words in each pair currently
produce different lemmas/stems); `test_unlisted_word_unaffected` PASSes already (nothing to
canonicalize for "surprise") — that's expected and fine, it's a regression guard, not something
this change is supposed to newly satisfy.

- [x] **Step 9: Add the `SPELLING_VARIANTS` lookup to `lemmatize_word()`**

In `rules/normalize.py`, add the import (alongside the existing `rules.english_stemming` /
`rules.phonetic` imports near the top of the file):

```python
from rules.canonical_forms import CONTRACTION_EXPANSIONS, SPELLING_VARIANTS
```

Then change the start of `lemmatize_word()` from:

```python
def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """
    ... (existing docstring, unchanged)
    """
    if language in STEMMABLE_LANGUAGES:
        return stem_english_word(word)
```

to:

```python
def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """
    ... (existing docstring, unchanged)
    """
    word = SPELLING_VARIANTS.get(word.lower(), word)
    if language in STEMMABLE_LANGUAGES:
        return stem_english_word(word)
```

(Only that one new line is added; everything else in the function — the
`LEMMATIZABLE_LANGUAGES` branch, the `morph.parse()` call, the `is_known` check — is unchanged.)

- [x] **Step 10: Run the tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -k SpellingVariants -v`
Expected: PASS (6 passed)

- [x] **Step 11: Write the failing tests for contraction expansion**

Add to `tests/rules/test_normalize.py`, near `TestNormalizeKeepDigitTokens` (the other
`normalize()`-level behavioral test class):

```python
class TestNormalizeContractionExpansion:
    def test_apostrophe_form_and_bare_form_produce_same_lemmas(self):
        morph = make_morph()
        assert normalize("don't", morph) == normalize("dont", morph)

    def test_contraction_and_full_phrase_overlap(self):
        morph = make_morph()
        assert normalize("dont", morph) & normalize("do not", morph)

    def test_expansion_result_is_not_empty(self):
        morph = make_morph()
        assert normalize("dont", morph) == {"not"}

    def test_short_expanded_part_dropped_like_any_other_short_word(self):
        morph = make_morph()
        # "is" (2 chars) drops below the default min_length=3, same as any other short word
        assert normalize("isnt", morph) == {"not"}

    def test_unlisted_word_flows_through_normal_path(self):
        morph = make_morph()
        # Cyrillic, not Latin -- can't possibly match a CONTRACTION_EXPANSIONS key (all
        # apostrophe-stripped Latin forms), so this cleanly proves the normal path is
        # untouched. Same word/expected-lemma pair as the existing
        # TestNormalizeLanguageGating.test_language_none_reproduces_default_behavior test.
        assert normalize("работе", morph) == {"работа"}
```

- [x] **Step 12: Run the tests to verify they fail**

Run: `pytest tests/rules/test_normalize.py -k ContractionExpansion -v`
Expected: `test_apostrophe_form_and_bare_form_produce_same_lemmas`,
`test_contraction_and_full_phrase_overlap`, and `test_expansion_result_is_not_empty` FAIL
("don't"/"dont" currently produce their own literal (differing) lemmas, not `{"not"}`);
`test_short_expanded_part_dropped_like_any_other_short_word` also FAILs the same way;
`test_unlisted_word_flows_through_normal_path` PASSes already (regression guard).

- [x] **Step 13: Add the `CONTRACTION_EXPANSIONS` handling to `normalize()`**

In `rules/normalize.py`, change the start of `normalize()`'s loop body from:

```python
    result: set[str] = set()
    for word in tokenize(text):
        if len(word) < min_length:
            continue
        if word.isdigit():
            if keep_digit_tokens:
                result.add(word)
            continue
        lemma = lemmatize_word(word, morph, language)
```

to:

```python
    result: set[str] = set()
    for word in tokenize(text):
        expansion = CONTRACTION_EXPANSIONS.get(word.lower().replace("'", ""))
        if expansion is not None:
            for part in expansion:
                if len(part) >= min_length:
                    result.add(lemmatize_word(part, morph, language))
            continue

        if len(word) < min_length:
            continue
        if word.isdigit():
            if keep_digit_tokens:
                result.add(word)
            continue
        lemma = lemmatize_word(word, morph, language)
```

Everything after this point in the function (the `result.add(lemma)` line, the subreddit-OCR-
artifact handling, the trailing-doubled-letter handling, and the final `return result`) is
unchanged — those blocks still run for every word that *isn't* a contraction match, exactly as
before.

- [x] **Step 14: Run the tests to verify they pass**

Run: `pytest tests/rules/test_normalize.py -k ContractionExpansion -v`
Expected: PASS (5 passed)

- [x] **Step 15: Run the full `tests/rules/` root**

This task modifies two functions (`tokenize()`, `lemmatize_word()`, `normalize()`) that every
other test file in this root exercises indirectly (`test_concept_tagger.py`, `test_engine.py`,
`test_english_stemming.py`, `test_lang_plausibility.py`, `test_phonetic.py`) — run the whole root
as a regression check, not just this one file.

Run: `pytest tests/rules/ -v`
Expected: all pass, no new failures.

- [x] **Step 16: Commit**

```bash
git add rules/canonical_forms.py rules/normalize.py tests/rules/test_normalize.py
git commit -m "feat: add search canonization (Cyrillic yo, spelling variants, contractions)"
```

- [x] **Step 17: Manual rollout note (not part of this commit)**

This step is a reminder for whoever runs this against a real environment, not something to do as
part of implementing this task: per the spec's Rollout section, `build_ocr_lemmas.py` needs a
**full** (non-incremental) re-run against each environment (metal/general/it) afterward to rebuild
`ocr_lemmas` with the new canonizations applied — incremental mode would skip already-indexed
images and miss the change for existing content. Do not run this as part of this task; it's an
operational step for whoever deploys this change to a real environment.
