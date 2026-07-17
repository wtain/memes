# trends_batch Russian lemmatization

## Context

`batch/trends_batch.py` runs GLiNER NER (`batch/trends/processing.py::Processor`)
over each configured trend source's fetched article text, and tallies
`label:entity_text` mention counts per run (`process_source()` in
`trends_batch.py`) before persisting them as `trends_run_results` rows
(`label`, `name`, `value`) via `TrendsRunResultRepository`.

The only currently-seeded source, Meduza (`batch/trends/seed_sources.py`), is
a Russian-language news source. Russian is a highly inflected language, so
GLiNER frequently extracts the same real-world entity as several different
surface strings depending on grammatical case — e.g. "Путин" (nominative),
"Путина" (genitive/accusative), "Путину" (dative) all refer to the same
person. Today's exact-string `Counter` key (`f"{label}:{entity_text}"`)
treats each of these as a distinct entity, fragmenting what should be one
trend into several smaller ones, both within a single run's counts and
across `trends_run_results` history (`name` is used as an exact-match filter
in `TrendsRepository.get_history`/`get_entries_for_run`, and as the
`/recommendations?q=` search-link target in the frontend's
`TrendRunEntries.tsx`).

The OCR pipeline has the same class of problem (inflected word forms
fragmenting a bag-of-words) and already solves it: `rules/normalize.py`
provides `make_morph()`/`lemmatize_word()`, backed by `pymorphy3` (a
Russian/Ukrainian morphological analyzer — only `pymorphy3-dicts-ru` is
installed, per `requirements.txt`), used by `batch/build_bow.py` to reduce
OCR'd words to their dictionary normal form before counting. This spec
brings the same lemmatization to `trends_batch.py`'s entity counting.

A structurally related but separate problem — that `rules/normalize.py` is
currently applied *unconditionally* to all OCR text regardless of detected
language, and that its tokenizer strips internal punctuation like hyphens —
is being addressed by two sibling specs, not this one: an OCR
language-gating spec (in progress at the time of writing) and the already
committed `docs/superpowers/specs/2026-07-17-ocr-tokenize-punctuation-preservation-design.md`.
This spec's `lemmatize_phrase()` (below) is deliberately independent of
`tokenize()` and does not require either sibling spec to land first, though
it's written to benefit automatically if/when they do.

Deliberately out of scope:
- Backfilling/re-normalizing `name` values already stored in
  `trends_run_results` from past runs. This is forward-looking only — past
  runs keep their raw, unmerged names; only runs after this ships get
  merged counts.
- Any language other than Russian. No other language is used by any
  currently-seeded or connector-supported source, and no other pymorphy3
  dictionary is installed.

## Language resolution

New `resolve_language(source, settings) -> str | None` in
`batch/trends/resolution.py`, mirroring the existing `resolve_labels()` /
`resolve_model()` pattern exactly:

```python
def resolve_language(source, settings) -> str | None:
    extraction = source.extraction or {}
    language = extraction.get("language")
    if language:
        return language
    return settings.get("trends.language")
```

There is no hardcoded default and no new `trends.language` key added to
`environments/settings.yaml` — language is opt-in, declared per source via
`extraction.language`, since different sources may cover different
languages. The Meduza seed source
(`batch/trends/seed_sources.py::MEDUZA_SOURCE`) gets
`"extraction": {"language": "ru"}` added explicitly. Sources with no
declared language (and no `trends.language` fallback configured) resolve to
`None`, meaning "do not lemmatize" — identical to how `resolve_model()`
already returns `None` when nothing configures a model.

## Lemmatization

New `lemmatize_phrase(text: str, morph: pymorphy3.MorphAnalyzer) -> str` in
`rules/normalize.py`, alongside the existing `lemmatize_word()`/
`make_morph()`:

```python
def lemmatize_phrase(text: str, morph: pymorphy3.MorphAnalyzer) -> str:
    """Lemmatize each whitespace-delimited chunk of text, preserving
    internal punctuation (e.g. hyphens in compound names) and word order."""
    return " ".join(lemmatize_word(chunk, morph) for chunk in text.split())
```

This deliberately does **not** reuse `tokenize()`: `tokenize()` is built for
bag-of-words extraction, where dropping punctuation and losing word order is
fine (the OCR pipeline only cares about which words appeared, not how they
were arranged). Trend entity names need to stay as a single, ordered,
readable phrase, and must not fragment a compound proper noun like
"Санкт-Петербург" across the hyphen — `text.split()` preserves that
structure by only splitting on whitespace, at the cost of not stripping
punctuation attached to a word (acceptable here because GLiNER entity spans
are already clean substrings of the source text, not raw OCR noise).

Per the earlier design decision to match the OCR bag-of-words precedent
directly, the output is the lemma itself — lowercase, no separate "display
form" preserving original casing.

## Wiring into trends_batch.py

`main()` creates `morph = make_morph()` once, unconditionally (negligible
one-time cost, same as `build_bow.py` already accepts), and holds it for the
lifetime of the run alongside the existing `processor = Processor()`.

`process_source()` gains two parameters: `language: str | None` and `morph`
(the shared `pymorphy3.MorphAnalyzer`, needed only when `language == "ru"`):

```python
def process_source(source, connector, processor: Processor, labels: list[str],
                    model_name: str, language: str | None, morph) -> Counter:
    trends = Counter()
    data = connector.fetch()
    for item in data:
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            if language == "ru":
                entity_text = lemmatize_phrase(entity_text, morph)
            trends[f"{label}:{entity_text}"] += 1
    return trends
```

`main()`'s loop resolves `language = resolve_language(source, settings)`
alongside the existing `labels`/`model_name` resolution, and passes both
`language` and the run-level `morph` through to `process_source()`. Sources
whose resolved language is not `"ru"` (including `None`) are completely
unaffected — `entity_text` flows through exactly as today, preserving
original casing and inflection, and `morph` is simply unused for that call.

`Processor` (GLiNER extraction) is intentionally left untouched: it has one
job (named-entity recognition) and has no reason to know about languages or
lemmas. Lemmatization is a normalization concern applied to its output, one
layer up in `process_source()`.

## Testing

- `tests/rules/` (or a new file there, matching the existing split of
  `rules/normalize.py` coverage): unit tests for `lemmatize_phrase()` —
  single word, multi-word phrase, hyphenated compound stays joined,
  already-nominative input is idempotent.
- `tests/batch/test_trends_resolution.py`: add `resolve_language()` cases
  mirroring the existing `resolve_labels`/`resolve_model` tests (source
  override present, falls back to `settings.get("trends.language")`, falls
  back to `None` when neither is set).
- `tests/batch/test_trends_batch.py`: extend `process_source()` coverage
  (already exercised via `_FakeConnector`/`_FakeProcessor`) with a case
  where two differently-inflected mentions of the same Russian entity
  collapse into one `Counter` entry when `language="ru"`, and a case
  confirming they stay separate when `language` is `None`/non-`"ru"`.

## Documentation

`trends_batch` has been added to CLAUDE.md's "Batch pipeline (execution
order)" list (it existed but wasn't documented there before this change).
No further CLAUDE.md changes are required by this spec — the language-gating
behavior is source config, not a CLI/config surface change to the script
itself.
