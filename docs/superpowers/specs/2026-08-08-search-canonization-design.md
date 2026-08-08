# Search Canonization — Design

Status: done
Plan: docs/superpowers/plans/2026-08-08-search-canonization.md

**Date:** 2026-08-08.

Adds two narrow, fixed-rule text canonizations to `rules/normalize.py` — British/American
English spelling variants and negative-contraction expansion — so equivalent forms match each
other across the rules engine, `build_bow.py`'s vocabulary extraction, and search. This is the
last item from the earlier "disambiguate and draft specs" batch (alongside `remove_singletons`,
`build_image_embeddings` progress/metrics, and the two ingestion specs, all already merged this
session); the user chose "narrow, fixed rules" over a general phonetic/fuzzy-matching system when
this was first triaged.

A third canonization, Cyrillic ё→е, was designed, implemented, and then **removed** after the
final whole-branch review found it was both unnecessary and actively harmful — see "Investigated
and rejected: Cyrillic ё→е" below. This spec was updated in place to reflect that outcome rather
than being left describing a feature that no longer ships.

---

## Motivation

Two concrete, currently-unaddressed match failures:

1. **British/American spelling.** `rules/english_stemming.py`'s Snowball stemmer recognizes
   `-ize` as a suffix to strip but has no equivalent rule for `-ise` — the two spellings stem to
   different results, so e.g. "categorise" and "categorize" don't match today.
2. **Contractions.** "don't", "dont" (OCR frequently drops apostrophes), and "do not" currently
   produce three different, non-overlapping lemma results — none of the three forms matches any
   of the others.

## Scope

**In scope:** `rules/normalize.py` (integration points) and a new `rules/canonical_forms.py`
(the two fixed lookup tables: `SPELLING_VARIANTS`, `CONTRACTION_EXPANSIONS`).

**Applies everywhere `rules/normalize.py` is used** — the rules engine (`rules/concept_tagger.py`,
which calls `normalize()`), search (`batch/utils/ocr_lemmas.py` at index time and
`repository/ocr_lemmas.py` at query time, both call `normalize()`), and, for the
`lemmatize_word()`-level piece only (spelling variants — not contraction expansion, see below),
`build_bow.py` (calls `tokenize()`/`lemmatize_word()` directly, not `normalize()`).

**Investigated and rejected: Cyrillic ё→е.** The original design canonicalized ё→е at
tokenize-time on the premise that "всё" and "все" don't match today. The final whole-branch review
found that premise false: pymorphy3 is ё-*restoring* — `morph.parse("все")[0].normal_form` is
already `"всё"` for known words, verified directly against the real analyzer both before and
after the change (identical output). The tokenize-time ё→е fold therefore did nothing for its own
motivating case, and only changed behavior on paths where pymorphy3 does *not* restore ё —
concept-vocabulary loading (`lemmatize_word_autodetect()`, which never calls `tokenize()`) and
non-lemmatizable-language OCR rows — where it actively regressed real data: 15 ё-containing
concept-vocabulary entries in the `general` environment's `concepts.general.yaml` would have
silently stopped firing (OCR text and vocabulary would no longer agree on the ё-containing forms),
8 existing `ImageTag.value` rows containing ё (stored verbatim, never normalized) would have
become unsearchable, and a fresh index/query mismatch would have appeared for Cyrillic OCR rows
tagged a non-Russian language. None of this is fixable by re-running `build_ocr_lemmas.py` — it
lives in YAML vocabulary and stored tag values, outside what a lemma-index rebuild touches. Root
cause: the change canonicalized toward `е` at *input* time, while the pipeline's dominant lemma
authority (pymorphy3) already canonicalizes toward `ё` at *output* time — two opposing conventions
in one pipeline. Not pursued further (e.g. canonicalizing toward pymorphy3's own convention
instead, applied at lemma-output time and to `lemmatize_word_autodetect()` too, plus rewriting the
8 affected tag values) since it wasn't closing a real gap to begin with.

**Out of scope:**
- **A general suffix-transformation rule** (e.g. "any word ending in `-ise`, try `-ize`") instead
  of a fixed word list. Rejected: it has real false-positive collision risk — a blanket
  `-ise`→`-ize` rule would incorrectly conflate "prise" (to pry open) with "prize" (reward), two
  genuinely distinct English words. A fixed, curated list has no such risk since only intentionally
  listed pairs are affected.
- **Inflected forms not literally in the fixed list** (e.g. "categorising", not present even
  though "categorise" is). These flow through unchanged; the existing Snowball stemmer *may*
  additionally unify some of them post-canonicalization (since canonicalization runs before
  stemming and the American form's inflections are already stemmer-recognized), but this isn't
  guaranteed or specifically engineered for. Accepted limitation of a narrow first pass —
  extensible later based on real search-log evidence, not exhaustively enumerated now.
- **Positive/ambiguous contractions** ("it's", "that's", "let's", "I'm") — only negative "n't"
  contractions are covered (see Design below for why they're the clean case).
- **Other British/American spelling variant classes** beyond `-ise/-ize` (+ `-yse/-yze`),
  `-isation/-ization`, `-our/-or`, and `-re/-er` — e.g. not `-ogue/-og` ("catalogue/catalog"),
  `-ce/-se` ("licence/license"), or single/double-consonant differences
  ("travelling/traveling"). Extensible later the same way.
- **No new fallback tier in `repository/ocr_lemmas.py`.** Both canonizations run at
  normalization time (shared by index-build and query-time paths), so they're matched via the
  *existing* exact-lemma tier — no new query-time special-casing.
- **No DB schema change.** `ocr_lemmas` rows are already rebuilt by re-running
  `build_ocr_lemmas.py` — see Rollout below for why that rebuild must be full, not incremental.

## Design

### `rules/canonical_forms.py` (new module)

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

### Integration into `rules/normalize.py`

**Spelling variants** — inside `lemmatize_word()`, applied before the language-branching logic
(so it reaches every caller of `lemmatize_word()`, not just `normalize()`'s loop: `build_bow.py`
calls `lemmatize_word()` directly):

```python
from rules.canonical_forms import CONTRACTION_EXPANSIONS, SPELLING_VARIANTS

def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """..."""  # existing docstring, unchanged
    word = SPELLING_VARIANTS.get(word.lower(), word)
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

(Only the new `word = SPELLING_VARIANTS.get(word.lower(), word)` line and the import are new;
everything below is the existing function body, unchanged.)

**Contraction expansion** — inside `normalize()`'s loop, checked before the length/digit filters
(so a short contraction form wouldn't be blocked by the raw token's own length — none of the
listed forms are actually short, but the expanded *parts* need their own independent length
check regardless, which happens in the inner loop):

```python
def normalize(
    text: str,
    morph: pymorphy3.MorphAnalyzer,
    min_length: int = 3,
    language: str | None = None,
    keep_digit_tokens: bool = False,
) -> set[str]:
    """..."""  # existing docstring, unchanged
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
        result.add(lemma)
        # ... existing subreddit-artifact and trailing-punctuation-artifact handling,
        # unchanged, still operating on `word`
    return result
```

## Testing

`tests/rules/` (existing dedicated unit-test root for `rules/normalize.py` and friends — no DB,
no I/O, matching this repo's established pattern):

- **Spelling variants**: `lemmatize_word("categorise", morph)` equals
  `lemmatize_word("categorize", morph)`; at least one entry from each of the four covered classes
  (-ise/-ize, -isation/-ization, -our/-or, -re/-er); an unlisted word (e.g. "surprise") is
  unaffected — confirms this is a lookup, not a suffix rule.
  `lemmatize_word("categorise", morph, language="en")` (the `STEMMABLE_LANGUAGES` path) is also
  covered, confirming the canonicalization happens before stemming, not only in the pymorphy3
  branch.
- **Contraction expansion**: `normalize("don't")`, `normalize("dont")`, and `normalize("do not")`
  produce the same (non-empty) lemma set; an expanded part shorter than `min_length` is dropped
  from the result (mirroring existing digit/short-token behavior) without raising; an unlisted
  contraction-like token (e.g. "shant" if not in the table, or any word not matching a dict key)
  is unaffected and flows through the normal path.
- Full existing `tests/rules/` suite re-run to confirm no regression to unrelated behavior (e.g.
  the subreddit-OCR-artifact and trailing-doubled-letter handling in `normalize()`, which still
  operate on `word` after the new checks).

## Rollout

1. Add `rules/canonical_forms.py`.
2. Update `rules/normalize.py`: the `SPELLING_VARIANTS` lookup in `lemmatize_word()`, and the
   `CONTRACTION_EXPANSIONS` handling in `normalize()`.
3. Re-run `build_ocr_lemmas.py` (full mode, not `--incremental` — canonization changes what lemma
   an *already-indexed* image's OCR text produces, so incremental mode's "skip images that
   already have rows" would miss the change for existing images) against each environment
   (metal/general/it) to rebuild `ocr_lemmas` with the new canonizations applied. Required rollout
   step, not optional — same category as the original smart-search spec's initial backfill
   requirement.
4. No `CLAUDE.md`/runbook documentation changes needed — this doesn't add a new script, change any
   existing script's CLI/config surface, or add an API endpoint; it changes the *behavior* of
   shared normalization code already documented at a conceptual level (`CLAUDE.md`'s Rules engine
   section already says `rules/normalize.py` "is shared by both engines and `build_bow.py` — use
   it for all text normalization to keep behavior consistent") without changing that description's
   accuracy.
