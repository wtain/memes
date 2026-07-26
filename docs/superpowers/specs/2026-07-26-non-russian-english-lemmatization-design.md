# Smart Search: Non-Russian (English) Lemmatization — Design

Status: Approved, ready for implementation planning.

## Problem

Only Russian OCR text gets real morphological normalization today
(`rules/normalize.py`'s `LEMMATIZABLE_LANGUAGES = frozenset({"ru"})`, via pymorphy3).
English text just gets lowercased — "cats"/"cat", "running"/"run", "batteries"/"battery"
are all treated as unrelated tokens for search matching purposes.

This is a real gap, not a hypothetical one. Checked against the live corpus after the
`lang_score` cleanup (see `docs/superpowers/specs/drafts/2026-07-24-smart-search-leftovers-draft.md`
item 3 and its "OCR errors" follow-up): non-Cyrillic content is substantial across all
three environments even after filtering out mis-tagged/garbled duplicate OCR
attempts — `metal` 93.8%, `it` 79.6%, `general` 44.2% of `ocr_lemmas` rows. `metal` in
particular (a metal-music meme corpus) is genuinely English-dominant, not an artifact.

## Scope

**English only for this pass.** Spanish (`es`-tagged content also exists in the corpus)
is explicitly deferred — see the Query-time design section below for why the two
languages need to be handled carefully to avoid a regression, and why deferring Spanish
specifically (rather than doing both now) keeps this pass small and low-risk.

## Approach: lightweight stemming, not a full lemmatizer

Considered three options:

1. **spaCy** (industrial NLP, POS-aware lemmatization — "ran"→"run", "better"→"good").
   Rejected: a ~12-50MB per-language model to download/version, spaCy's own dependency
   tree, and it would be the first heavy-ML dependency in
   `Backend/requirements-backend.txt`, which has deliberately avoided that stack.
2. **NLTK + WordNetLemmatizer**. Rejected: needs a POS tagger to lemmatize well (naive
   use without one often leaves words unchanged) — not actually simpler than spaCy once
   done properly, just differently heavy.
3. **Lightweight rule-based stemmer (chosen)**: the standalone `snowballstemmer` PyPI
   package. Verified during design: 104KB, zero dependencies, pure Python. Doesn't
   produce a real dictionary word ("batteries"→"batteri", "arguing"→"argu") and won't
   unify irregular pairs a real lemmatizer would ("better"/"good" stay distinct) — but
   `OCRLemma.lemma` was never a real dictionary word for Russian either (case-declined
   forms collapse to pymorphy3's `normal_form`, not something users ever see), so this
   is consistent with how the index already works. Verified against real corpus
   vocabulary during design (see table below) — unifies plurals and common verb
   conjugations correctly, leaves proper nouns alone.

| Input | Stem | Input | Stem |
|---|---|---|---|
| cats / cat | cat | metalhead(s) | metalhead |
| running / run | run | toronto | toronto (unchanged) |
| friends / friend | friend | hanneman | hanneman (unchanged) |
| batteries / battery | batteri | better / good | better / good (don't unify — accepted) |

This mirrors the reasoning that led to phonetic matching over an LLM for erratives:
pick the cheapest mechanism that solves the actual matching problem, not the most
linguistically complete one.

## Architecture

New module `rules/english_stemming.py` (mirrors `rules/phonetic.py`'s pattern — a
single-purpose module wrapping one algorithm):

```python
import snowballstemmer

_stemmer = snowballstemmer.stemmer("english")


def stem_english_word(word: str) -> str:
    return _stemmer.stemWord(word.lower())


def is_latin_word(word: str) -> bool:
    """True if word consists entirely of lowercase Latin letters -- the
    only input stem_english_word() is meaningful for. Query-time-only gate
    (see repository/ocr_lemmas.py); index-time dispatch uses each OCR row's
    own detected language tag instead, same as the Russian/phonetic case."""
    ...  # ^[a-z]+$
```

### Index time: new explicit-language branch

`rules/normalize.py::lemmatize_word` gains one new branch, checked first:

```python
def lemmatize_word(word, morph, language=None):
    if language == "en":
        return stem_english_word(word)
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    ...  # unchanged
```

Each OCR row already carries its own detected `language` (`en`/`es`/`ru`/etc. — see
`Storage/models.py::OCRText.language`), so index-time dispatch is unambiguous: an
`"en"`-tagged row's tokens go straight to the stemmer. `LEMMATIZABLE_LANGUAGES` (still
just `{"ru"}`) is deliberately left untouched, not extended — it specifically means
"pymorphy3 can lemmatize this," a different mechanism than stemming, so conflating the
two into one set would mislead a future reader. A new, separate
`STEMMABLE_LANGUAGES = frozenset({"en"})` constant is added instead, for the same
"what mechanism handles this language code" clarity, even though `lemmatize_word`
itself just checks the literal string today (the constant documents intent and gives
future callers something to check against, matching how `LEMMATIZABLE_LANGUAGES` is
used elsewhere).

### Query time: a new fallback tier, not a change to the primary lemma

**This is the part that took two iterations to get right during design — recorded here
because the reasoning matters for anyone touching this later.**

First attempt: make `lemmatize_word`'s `language=None` path (used at query time, since
a raw query string has no per-token language tag) detect Latin-script tokens via
`is_latin_word` and stem them unconditionally, the same way the Cyrillic path already
works. **Rejected**: Spanish is also Latin-script. `is_latin_word` can't distinguish
`en` from `es`, so a Spanish query token would get run through the *English* stemmer,
while Spanish-tagged index rows (not touched by the new `language == "en"` branch)
still store the plain lowercased form. Query and index would diverge for Spanish
content that matches correctly *today* — a real regression, not just a missed
opportunity, and exactly the "index/query symmetry risk" already flagged in the
erratives backlog notes.

**Fix**: stemming becomes a new *fallback* tier in
`repository/ocr_lemmas.py::matching_image_ids`, tried only after exact match already
fails — mirroring how trigram and phonetic fallback already work, and specifically
*not* a change to what `normalize()`/`lemmatize_word()` return for `language=None`.
`lemmatize_word` needs no change at all for the query path; `is_latin_word` lives in
`repository/ocr_lemmas.py`, gating a new `_stem_lemma_ids` helper — same placement
pattern as `is_cyrillic_word`/`_is_known_word` from the phonetic-erratives feature.

```python
# imported directly: from rules.english_stemming import is_latin_word, stem_english_word
# -- same pattern as is_cyrillic_word's direct import for the phonetic feature,
# no local wrapper needed.


async def _stem_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """Query-time-only fallback: exact-matches the lemma's English stem
    against OCRLemma.lemma, since the stored value for "en"-tagged rows
    already IS the stem, computed at index time by the same
    stem_english_word(). OCRLemma only, not ImageTag -- same scope
    reduction as the phonetic feature (tags are a controlled vocabulary,
    not raw OCR text)."""
    stem = stem_english_word(lemma)
    result = await session.execute(
        select(OCRLemma.image_id).where(OCRLemma.lemma == stem)
    )
    return {row[0] for row in result.all()}


# in matching_image_ids's loop:
for lemma in lemmas:
    lemma_ids = await _exact_lemma_ids(session, lemma)
    if not lemma_ids:
        if is_latin_word(lemma):
            lemma_ids = lemma_ids | await _stem_lemma_ids(session, lemma)
        if len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH:
            lemma_ids = lemma_ids | await _fuzzy_lemma_ids(session, lemma)
            if (is_cyrillic_word(lemma) and ... ):
                lemma_ids = lemma_ids | await _phonetic_lemma_ids(session, lemma)
    ...
```

Trace through both cases:
- **Spanish query `gatos`**: exact match on `gatos` tried first. If a Spanish-tagged
  row stored `gatos` unstemmed (today's behavior, unchanged), it matches immediately —
  the stemming fallback never runs. No regression.
- **English query `cats`**: exact match on `cats` fails (the index stores the stem,
  `cat`, for `"en"`-tagged rows). `is_latin_word("cats")` is true → `_stem_lemma_ids`
  computes `stem_english_word("cats")` = `"cat"` → exact-matches the stored stem.
- **Spanish query where exact match fails for an unrelated reason** (e.g. a genuine
  typo not in the index): the stemming fallback still runs (`is_latin_word` can't tell
  it's Spanish), producing some English-stemmed candidate that won't match anything
  meaningful in the index. Harmless — one extra lookup, never a wrong result, since it
  can only add matches, never suppress the (already-failed) exact match.

Stemming has **no length guard** (unlike trigram/phonetic's `FUZZY_MIN_LEMMA_LENGTH`):
it's deterministic rule-based suffix stripping, not a similarity search, so it doesn't
carry the same short-word false-positive risk that motivates the length guards
elsewhere. It's also **unioned alongside trigram+phonetic, not given priority over
them** — same reasoning as the phonetic design's "union, don't sequentially suppress"
choice: different fallback tiers catch different failure classes, and there's no
reason one should preempt another once exact match has already failed.

**Explicitly not fixed here** (see the separate deferred-ideas draft for the fuller
discussion): exact match still fully short-circuits *all* fallback tiers, including
this new one — if any image matches the raw query exactly, none of trigram, phonetic,
or stemming ever run, even though a fuzzier tier might have surfaced additional
legitimate results. This is a pre-existing property of the whole fallback-chain
architecture (not introduced by this feature), and stemming inherits it rather than
fixing it.

## Data flow / storage

No schema changes. This only changes what string `OCRLemma.lemma` stores for
`"en"`-tagged rows (a stem instead of a bare lowercase form) and what candidate strings
get tried at query time. Since `OCRLemma.lemma` was never guaranteed to be a real
dictionary word to begin with, this is invisible to every other part of the pipeline
(trigram index, phonetic index, `ImageTag` matching) — none of them care what produced
the stored string.

## Dependencies

`snowballstemmer` added to both `Backend/requirements-backend.txt` (needed by the
query-time path in `repository/ocr_lemmas.py`) and root `requirements.txt` (needed by
the batch pipeline, `batch/utils/ocr_lemmas.py`). Per `Backend/requirements-backend.txt`'s
own documented convention, it must be regenerated via a clean-venv `pip freeze`, not
hand-edited — the implementation plan should call this out as its own step.

## Testing

- `tests/rules/test_english_stemming.py` (no DB, no I/O): pins the sample words
  verified during design (`cats`/`cat`, `running`/`run`, `batteries`/`battery`,
  `metalhead`/`metalheads`, proper nouns unchanged), plus `is_latin_word` edge cases
  (Cyrillic, mixed script, digits, empty string) — same structure as
  `test_phonetic.py`.
- `tests/rules/test_normalize.py`: a case confirming `lemmatize_word(word, morph,
  language="en")` stems rather than lowercasing, and that `"ru"`/language-gated
  behavior is unaffected.
- `tests/integration/test_ocr_lemmas_repository.py`, three new cases:
  1. An `"en"`-tagged indexed lemma (e.g. `cat`, stored via the stemmed index path)
     matches a query using a different inflection (`cats`) — proves the query-time
     fallback actually reaches the same stem the index-time path produced.
  2. **Regression guard, directly testing the concern that drove the query-time
     redesign**: a Spanish word indexed and queried identically (both unstemmed, e.g.
     `gatos`) still matches via exact match, unaffected by the new stemming fallback
     existing at all.
  3. A short English word (below `FUZZY_MIN_LEMMA_LENGTH`) still reaches the stemming
     fallback (proving stemming's lack of a length guard is real, not accidental).

## Known, disclosed limitations

- **Spanish**: out of scope for this pass (see Scope section). The query-time design
  is specifically shaped so adding Spanish later doesn't require touching this feature
  again — Spanish would get its own `language == "es"` index-time branch and its own
  query-time fallback tier, following the same pattern.
- **Irregular forms** a real lemmatizer would unify but a stemmer can't (`better`/
  `good`) — accepted tradeoff of the lightweight-stemmer choice.
- **Hyphenated compounds and contractions** (`well-known`, `don't`) aren't stemmed —
  `is_latin_word`'s regex (`^[a-z]+$`) deliberately only matches plain letter
  sequences, mirroring `is_cyrillic_word`'s equally narrow scope from the phonetic
  feature. **Noted for a follow-up pass, not tackled now** — captured in
  `docs/superpowers/specs/drafts/2026-07-24-smart-search-leftovers-draft.md`.
- **Fallback-tier short-circuiting** (exact match suppresses all fallback tiers,
  including this new one, even when a fallback might add legitimate results) is a
  pre-existing architectural property this feature inherits rather than fixes — see
  the deferred-ideas draft for the fuller discussion, including the "run all tiers in
  parallel and rank" and "progressive/async search" ideas raised during this feature's
  design.
