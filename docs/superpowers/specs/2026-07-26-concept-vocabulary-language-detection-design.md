# Concept Vocabulary Language Detection — Design

Status: Approved, ready for implementation planning.

## Problem

`rules/normalize.py`'s shared `lemmatize_word` recently gained a `language == "en"`
dispatch branch (see
`docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md`), so
`"en"`-tagged OCR text rows now get stemmed instead of just lowercased. That change's
own final whole-branch review found a consequence its design didn't account for:
`lemmatize_word` is shared infrastructure, not search-matching-private. Two other
consumers call it with each OCR row's own real `language` tag, so they now stem
English OCR text too — while the hand-curated **vocabulary** they compare that text
against is loaded with no `language` argument at all (the `language=None` default,
which lowercases Latin-script words but never stems them). This is the same class of
index/query (here: vocabulary/text) asymmetry the search feature was careful to avoid,
just surfacing in a different, unplanned place.

Concretely, once vocabulary is symmetric with text: a concept vocabulary word stored
as a base form (`"cat"`) would newly match English OCR text it stems to (`"cats"` →
`"cat"`), while a vocabulary word stored as an inflected form (`"cats"`) would need to
also be stemmed to keep matching. Left as-is, this shift happens unevenly and
unpredictably depending on which form each vocabulary entry happens to already use.

## Scope: three call sites, one shared cause

Checked every caller of `lemmatize_word(word, morph)` with no `language` argument.
Two are legitimately text-analysis paths with no vocabulary-symmetry concern and are
**not** touched by this design:
- `ConceptTagger.tag()`'s OCR-text path and `build_bow.py::_build_ocr_bow` — both
  already receive each OCR row's real `language` tag from their callers.
- `build_bow.py::_build_descriptions_bow` (LLM-generated image descriptions) — a
  different kind of gap (no per-row language tag exists for descriptions at all,
  unlike OCR rows), and descriptions are analyzed text, not a curated vocabulary being
  matched against. Out of scope; worth its own consideration separately.

Three are genuine hand-curated vocabulary loads with the asymmetry:

1. `rules/concept_tagger.py::_load_concepts` — the concept vocabulary itself (the one
   the final review flagged directly).
2. `batch/build_bow.py::_load_ignore_lemmas` — the `bow.ignore_file` word list.
3. `batch/build_bow.py::_build_json_rules_lemma_set` /
   `_build_concepts_lemma_set` — builds the "already covered by rules/concepts"
   lemma set used to compute unmatched vocabulary for the concept-discovery pipeline
   (`build_lemma_clusters` / `draft_concepts_from_clusters`).

All three read hand-curated word lists (`concepts.<env>.yaml`, `tags.<env>.yaml`,
`ignore-words.<env>.json`, the legacy `rules.<env>.json`) and share the exact same
underlying cause, so they get the exact same fix.

## Why script-detection, not per-word metadata

Checked the actual data: individual concept entries mix languages **within the same
word list**. `concepts.general.yaml`'s `cringe` concept has both `cringe` (English) and
`кринж`/`ринж`/`кринжовый` (Russian) as entries in one `words:` list. There is no
single language to attach to a concept, or even to a whole file — only to each
individual word.

Rather than migrating every vocabulary file to declare per-word language explicitly
(real authoring burden, ongoing maintenance cost for every future addition), this
detects each word's own script directly, reusing the same script-check functions
already built for search matching: `is_cyrillic_word` (from
`rules/phonetic.py`, built for the erratives feature) and `is_latin_word` (from
`rules/english_stemming.py`, built for the English-lemmatization feature). Vocabulary
words are already almost always pure single-script tokens ("opeth", "кринж") — this is
reliable in practice without needing new data.

## Design

New function in `rules/normalize.py`:

```python
def lemmatize_word_autodetect(word: str, morph: pymorphy3.MorphAnalyzer) -> str:
    """
    Like lemmatize_word, but for callers with no external language signal
    at all (hand-curated vocabulary in concepts/tags/ignore-word files,
    as opposed to OCR text rows which carry their own detected language).
    Detects the word's own script and dispatches accordingly, reusing
    is_cyrillic_word/is_latin_word (the same checks matching_image_ids
    uses at query time) instead of requiring per-word language metadata
    in the data files.
    """
    lowered = word.lower()
    if is_cyrillic_word(lowered):
        return lemmatize_word(word, morph, language="ru")
    if is_latin_word(lowered):
        return lemmatize_word(word, morph, language="en")
    return lemmatize_word(word, morph)
```

Requires two new imports at the top of `rules/normalize.py`:
`from rules.phonetic import is_cyrillic_word` and
`from rules.english_stemming import is_latin_word`. Neither introduces a circular
import — both modules are self-contained (only `re` plus, for the latter,
`snowballstemmer`) and import nothing from `rules/normalize.py`.

All six identified call sites (2 in `_load_concepts`, 1 in `_load_ignore_lemmas`, 1 in
`_build_json_rules_lemma_set`, 2 in `_build_concepts_lemma_set`) change from
`lemmatize_word(word, morph)` to `lemmatize_word_autodetect(word, morph)`. No other
call sites change.

## Collision risk — why no gate is needed here

The English-lemmatization design's final review specifically asked whether stemming
hand-curated vocabulary risks the same kind of false-positive collision that phonetic
matching needed an `is_known` gate to avoid. The risk profile here is different and
lower:

- Phonetic matching operated on **uncurated live user queries** — any word a user
  types, including genuine unrelated real words that happen to sound alike.
- This operates on **hand-curated vocabulary** a human wrote deliberately, and
  stemming is deterministic rule-based suffix-stripping, not a similarity/guessing
  algorithm — there's no risk-class equivalent to "two unrelated words coincidentally
  sound the same."
- `concept_tagger.py` already has an existing homonym-detection warning
  (`"Homonym: word '%s' appears in concepts %s"`, logged whenever two vocabulary
  entries lemmatize to the same value) that fires automatically the moment
  `ConceptTagger.load()` runs. This already-existing mechanism surfaces any new
  stem-collision this change introduces as a visible warning, not a silent failure —
  no new gate needed on top of it.

**Disclosed limitation found during the final whole-branch review — a false-*negative*
in the opposite direction, not covered by the collision-risk analysis above.**
`lemmatize_word_autodetect` treats *any* pure-Latin-script vocabulary word as English
and stems it unconditionally. But the OCR-text side only stems when the row's own
detected `language` is actually `"en"` — rows tagged `"es"` (Spanish, also Latin-script)
or `"unknown"` (NULL/undetected, the fallback `batch/build_tags_from_ocr.py:55`
substitutes via `language or "unknown"`) stay lowercase-only, unstemmed. Consequence: an
**inflected** English vocabulary entry (e.g. `"cats"`, stemmed at load time to `"cat"`)
will no longer match the same inflected word in an `"es"`/`"unknown"`-tagged OCR row
(still `"cats"`, unstemmed) — a match that *did* work before this branch, when both
sides were plain-lowercased. This is accepted, not fixed: most vocabulary entries are
either base forms or proper nouns that stem to themselves (`"opeth"`, `"metallica"`
unchanged), so the practical blast radius is narrow (only inflected common-word English
vocabulary matched against non-`en`-tagged rows), and the dominant corpus is `en`/`ru`.
Same tradeoff class already noted in `lemmatize_word`'s own docstring for the
`STEMMABLE_LANGUAGES`/`LEMMATIZABLE_LANGUAGES` gap generally.

**Second disclosed limitation, also found during that review**: the vocabulary sets this
change touches (`ignore_lemmas`, `rules_lemmas` in `batch/build_bow.py::main()`) are
built once and applied to *either* the OCR-BOW or the descriptions-BOW output, whichever
`settings.BOW.TEXT_SOURCE` selects — not just the OCR path this design otherwise
reasons about. `_build_descriptions_bow` itself is untouched (still plain
`lemmatize_word(word, morph)`, unstemmed), but if `TEXT_SOURCE` were ever set to
`descriptions` in any environment, its unstemmed output would be compared against these
now-stemmed vocabulary sets — the same asymmetry class as above, on a path this design
otherwise describes as fully out of scope. Not active today: no environment's
`settings.yaml` sets `text_source: descriptions`. Worth revisiting if that path is ever
enabled.

## Testing

- `tests/rules/test_normalize.py`: new test class for `lemmatize_word_autodetect`
  directly — a Cyrillic word gets real pymorphy3 lemmatization, a Latin word gets
  stemmed, and a digit/mixed-script word falls through to the unchanged plain-lowercase
  behavior.
- `tests/rules/test_concept_tagger.py`: one new test using the existing `_make_engine`
  fixture helper, proving vocabulary/text symmetry end-to-end — a concept with
  vocabulary word `"cat"` matches OCR text `"cats"` when tagged `language="en"`
  (calling `engine.tag(text, language="en")` directly; the existing test helper's
  `_tags(engine, text)` convenience wrapper calls `.tag(text)` with no language, which
  wouldn't exercise this path — matches how `batch/build_tags_from_ocr.py` actually
  calls `.tag()` in production, passing each row's real detected language).
- New `batch/tests/test_build_bow_vocab.py` (no existing test file covers
  `build_bow.py` at all today): unit tests for the three modified functions, using temp
  fixture files (JSON/YAML), proving each now produces stemmed English / lemmatized
  Russian vocabulary entries instead of plain-lowercased ones.

## Data flow / storage

No schema, settings, or migration changes. This changes only what string a handful of
in-memory vocabulary-loading functions compute from already-existing data files —
nothing is persisted differently, and nothing about `OCRLemma`/search matching changes
(that feature's own vocabulary-equivalent, `OCRLemma.lemma` itself, was already fixed by
the English-lemmatization feature; this is exclusively about the *tagging*/BOW side).

## Operational note

Per the English-lemmatization design's own deferred-decision framing: this change only
actually shifts tag output once `batch/build_tags_from_ocr.py` and/or `batch/build_bow.py`
are next rerun in a given environment — merging this branch doesn't retroactively
change any already-computed tags. Rerunning those batch jobs in `metal`/`general`/`it`
after this merges is a live-data operation needing its own explicit go-ahead per
environment, same as every other batch rebuild in this project's history.
