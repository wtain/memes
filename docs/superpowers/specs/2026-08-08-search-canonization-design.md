# Search Canonization — Design

Status: planned
Plan: docs/superpowers/plans/2026-08-08-search-canonization.md

**Date:** 2026-08-08.

Adds three narrow, fixed-rule text canonizations to `rules/normalize.py` — Cyrillic ё→е,
British/American English spelling variants, and negative-contraction expansion — so equivalent
forms match each other across the rules engine, `build_bow.py`'s vocabulary extraction, and
search. This is the last item from the earlier "disambiguate and draft specs" batch (alongside
`remove_singletons`, `build_image_embeddings` progress/metrics, and the two ingestion specs, all
already merged this session); the user chose "narrow, fixed rules" over a general
phonetic/fuzzy-matching system when this was first triaged.

---

## Motivation

Three concrete, currently-unaddressed match failures:

1. **Cyrillic ё/е.** Casual Russian typing overwhelmingly substitutes е for ё (many keyboards
   make ё inconvenient to type), so "всё" and "все" are extremely common variant spellings of
   words that should match each other in search. This is *not* already solved by the existing
   phonetic-erratives fallback (`rules/phonetic.py`'s `russian_metaphone`, wired into
   `repository/ocr_lemmas.py`'s `_phonetic_lemma_ids`): that tier only fires when the query lemma
   is *not* a pymorphy3-recognized dictionary word (`_is_known_word` is `False`) — "все" and "всё"
   are both real, known dictionary words, so the phonetic fallback is explicitly skipped for
   exactly this case.
2. **British/American spelling.** `rules/english_stemming.py`'s Snowball stemmer recognizes
   `-ize` as a suffix to strip but has no equivalent rule for `-ise` — the two spellings stem to
   different results, so e.g. "categorise" and "categorize" don't match today.
3. **Contractions.** "don't", "dont" (OCR frequently drops apostrophes), and "do not" currently
   produce three different, non-overlapping lemma results — none of the three forms matches any
   of the others.

## Scope

**In scope:** `rules/normalize.py` (ё→е, integration points) and a new `rules/canonical_forms.py`
(the two fixed lookup tables: `SPELLING_VARIANTS`, `CONTRACTION_EXPANSIONS`).

**Applies everywhere `rules/normalize.py` is used** — the rules engine (`rules/concept_tagger.py`,
which calls `normalize()`), search (`batch/utils/ocr_lemmas.py` at index time and
`repository/ocr_lemmas.py` at query time, both call `normalize()`), and, for the
`lemmatize_word()`-level pieces only (ё→е, spelling variants — not contraction expansion, see
below), `build_bow.py` (calls `tokenize()`/`lemmatize_word()` directly, not `normalize()`).

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
- **No new fallback tier in `repository/ocr_lemmas.py`.** All three canonizations run at
  normalization time (shared by index-build and query-time paths), so they're matched via the
  *existing* exact-lemma tier — no new query-time special-casing.
- **No DB schema change.** `ocr_lemmas` rows are already rebuilt by re-running
  `build_ocr_lemmas.py`; this change takes effect the next time that runs (full or incremental —
  a canonization applies at lemma-computation time regardless of mode).

## Design

### Cyrillic ё→е — `rules/normalize.py`

Extends the existing character-preprocessing translate table (currently normalizing en/em-dashes
and the smart apostrophe), applied before tokenization:

```python
_CHAR_NORMALIZE = str.maketrans({
    "–": "-",   # en dash
    "—": "-",   # em dash
    "’": "'",   # right single quotation mark / smart apostrophe
    "ё": "е",   # Cyrillic ё -> е -- casual typing overwhelmingly substitutes е for ё; not
    "Ё": "Е",   # already covered by the phonetic-erratives fallback, which only fires for
                # words pymorphy3 doesn't recognize (see design doc's Motivation)
})


def _normalize_chars(text: str) -> str:
    return text.translate(_CHAR_NORMALIZE)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(_normalize_chars(text))
```

(Renames `_JOINER_NORMALIZE`/`_normalize_joiners` to `_CHAR_NORMALIZE`/`_normalize_chars` since the
table is no longer only about joiner characters — same mechanism, more accurate name.)

Unconditional, whole-text substitution — no word-boundary or script gating needed, matching how
the existing dash/quote normalization already works.

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

- **ё→е**: `tokenize("всё")` and `tokenize("все")` produce the same token string; `normalize()`
  on text containing "всё" and text containing "все" (same word otherwise) produce overlapping
  lemma sets.
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
2. Update `rules/normalize.py`: the `_CHAR_NORMALIZE`/`_normalize_chars` rename + ё/Ё entries,
   the `SPELLING_VARIANTS` lookup in `lemmatize_word()`, and the `CONTRACTION_EXPANSIONS` handling
   in `normalize()`.
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
