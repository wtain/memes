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
is addressed by two sibling specs, not this one:
`docs/superpowers/specs/2026-07-17-ocr-lemmatization-language-gating-design.md`
and `docs/superpowers/specs/2026-07-17-ocr-tokenize-punctuation-preservation-design.md`.
This spec's `lemmatize_phrase()` (below) is deliberately independent of
`tokenize()` and does not require the tokenize spec to land first, though
it's written to benefit automatically if/when it does.

This spec does, however, take a small dependency on the language-gating
spec: rather than hardcoding a duplicate `language == "ru"` check in
`trends_batch.py`, it reuses that spec's `LEMMATIZABLE_LANGUAGES` constant
(see Wiring below) — one source of truth for "which languages does this
module's pymorphy3 lemmatizer support," shared across both pipelines instead
of drifting independently. `LEMMATIZABLE_LANGUAGES` is a tiny, foundational
constant (three lines) with no OCR-specific logic attached to it, so if this
spec is implemented first, add it to `rules/normalize.py` as part of this
work rather than waiting on the sibling spec; if implemented second, just
reuse what's already there.

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
are, by construction, contiguous substrings of the source text with
model-chosen boundaries — unlike raw OCR noise, which is why this spec
doesn't reuse `tokenize()`'s punctuation-stripping).

**Pre-implementation check, not yet performed:** the claim above that GLiNER
spans are "clean" (i.e. rarely include trailing punctuation like a comma
attached with no space) is a reasonable assumption based on how span-based
NER models are trained, but it hasn't been empirically verified against real
Meduza output the way the sibling OCR specs verified their pymorphy3/wordfreq
claims directly. Before implementing, run `Processor.process()` against a
sample of real Meduza article text and spot-check a few dozen extracted
`entity_text` values for attached punctuation. If it turns out spans
sometimes do carry trailing/leading punctuation, `lemmatize_phrase()` may
need a light strip (e.g. `text.strip(string.punctuation)` per whitespace
chunk) before lemmatizing — a small addition, not a redesign, if needed.

Per the earlier design decision to match the OCR bag-of-words precedent
directly, the output is the lemma itself — lowercase, no separate "display
form" preserving original casing.

## Wiring into trends_batch.py

`main()` creates `morph = make_morph()` once, unconditionally (negligible
one-time cost, same as `build_bow.py` already accepts), and holds it for the
lifetime of the run alongside the existing `processor = Processor()`.

`process_source()` gains two parameters, both defaulted so the existing
5-arg call shape keeps working: `language: str | None = None` and
`morph: pymorphy3.MorphAnalyzer | None = None` (the shared analyzer, needed
only when `language` is lemmatizable). The gate reuses
`LEMMATIZABLE_LANGUAGES` from `rules/normalize.py` (introduced by the
language-gating spec, or by this spec if implemented first — see Context)
instead of hardcoding `"ru"` again in a second place:

```python
from rules.normalize import LEMMATIZABLE_LANGUAGES, lemmatize_phrase

def process_source(source, connector, processor: Processor, labels: list[str],
                    model_name: str, language: str | None = None,
                    morph: pymorphy3.MorphAnalyzer | None = None) -> Counter:
    trends = Counter()
    data = connector.fetch()
    for item in data:
        text = item["text"]
        for entity_text, label in processor.process(text, model_name, labels):
            if language in LEMMATIZABLE_LANGUAGES:
                entity_text = lemmatize_phrase(entity_text, morph)
            trends[f"{label}:{entity_text}"] += 1
    return trends
```

Note this mirrors `lemmatize_word()`'s own membership check
(`language not in LEMMATIZABLE_LANGUAGES`) but isn't the identical
expression — `lemmatize_word()` treats `language=None` as "no signal, use
legacy behavior" (see the sibling spec), whereas here `None in
LEMMATIZABLE_LANGUAGES` is simply `False`, which is exactly the wanted
result: no declared language means don't lemmatize, full stop. The two
functions' `None` handling differs by design (see the "Note for callers
outside this module" docstring the sibling spec adds to `lemmatize_word()`);
sharing the *constant* doesn't require sharing that behavior.

The two existing tests in `tests/batch/test_trends_batch.py`
(`test_process_source_tallies_entities_across_items`,
`test_process_source_handles_entity_text_containing_colon`) call
`process_source(source, connector, processor, ["band"], "model-a")` with only
five positional arguments — the defaults above mean neither test needs to
change; they exercise the `language=None` (no lemmatization) path exactly as
before.

`main()`'s loop resolves `language = resolve_language(source, settings)`
alongside the existing `labels`/`model_name` resolution, and passes both
`language` and the run-level `morph` through to `process_source()`:

```python
async def main():
    processor = Processor()
    morph = make_morph()

    async with AsyncSessionLocal() as session:
        ...
        for source in sources:
            connector = get_connector(source.name, source.connector_type, source.config)
            labels = resolve_labels(source, settings)
            model_name = resolve_model(source, settings)
            language = resolve_language(source, settings)

            trends = process_source(source, connector, processor, labels, model_name, language, morph)
            ...
```

`batch/trends/seed_sources.py::MEDUZA_SOURCE` gets an `extraction` key added:

```python
MEDUZA_SOURCE = {
    "name": "Meduza",
    "connector_type": "api",
    "extraction": {"language": "ru"},
    "config": {
        "base_url": "https://meduza.io/api/w5/new_search",
        "locale": "ru",
        ...
    },
}
```

(`config.locale` is unrelated and untouched — it's the Meduza API's own
content-locale parameter, not the language signal this spec introduces; see
the "Language signal" decision in the Context section above.)

Sources whose resolved language is not in `LEMMATIZABLE_LANGUAGES`
(including `None`) are completely unaffected — `entity_text` flows through
exactly as today, preserving original casing and inflection, and `morph` is
simply unused for that call.

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
  confirming they stay separate when `language` is `None` or otherwise not
  in `LEMMATIZABLE_LANGUAGES`.

## Documentation

`trends_batch` has been added to CLAUDE.md's "Batch pipeline (execution
order)" list (it existed but wasn't documented there before this change).
No further CLAUDE.md changes are required by this spec — the language-gating
behavior is source config, not a CLI/config surface change to the script
itself.
