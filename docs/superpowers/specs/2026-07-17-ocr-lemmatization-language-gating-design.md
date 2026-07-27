# OCR Lemmatization Language Gating

Status: done
Plan: docs/superpowers/plans/2026-07-17-lemmatization-uniformity.md

**Date:** 2026-07-17
**Scope:** `rules/normalize.py`, `batch/build_bow.py`, `rules/concept_tagger.py`, `repository/images.py` (new methods only — see Design Decisions), `batch/build_tags_from_ocr.py`, new `tests/rules/test_normalize.py`, `tests/rules/test_concept_tagger.py`, `tests/integration/test_build_ocr_bow_lang_filter.py`

**Cross-reference:** a sibling spec, `docs/superpowers/specs/2026-07-17-ocr-tokenize-punctuation-preservation-design.md`, also modifies `rules/normalize.py` (it changes `tokenize()`'s regex; this spec changes `lemmatize_word()`/`normalize()`'s signature and adds `LEMMATIZABLE_LANGUAGES`). The two touch different functions in the same file and compose without conflict, but whoever implements should merge both sets of changes into one final `rules/normalize.py` rather than applying either spec's code blocks as a wholesale file replacement — the `tokenize()` shown "unchanged" below is only unchanged *relative to this spec*, not relative to the sibling spec.

---

## Summary

`rules/normalize.py::lemmatize_word()` is backed by a single `pymorphy3.MorphAnalyzer()` — a morphological analyzer whose only installed dictionaries are Russian (`pymorphy3-dicts-ru`; there is no `pymorphy3-dicts-uk` in any requirements file). Two live pipeline call sites apply it to OCR-derived text **unconditionally**, even though each already has (or can easily get) a per-row detected `language` (`en`/`es`/`ru`, from EasyOCR):

- `batch/build_bow.py::_build_ocr_bow` — reads `language` per row and buckets output by it, but still calls `lemmatize_word(word, morph)` with no language gating.
- `rules/concept_tagger.py::ConceptTagger.tag()` (used by `batch/build_tags_from_ocr.py`, which is in the main batch pipeline) — has no language awareness at all, and its caller doesn't even fetch the `language` column today.

This spec adds an optional `language` parameter to `rules/normalize.py`'s `lemmatize_word()` / `normalize()`, threads the OCR row's detected language through both call sites, and skips the Russian analyzer entirely (falling back to a plain lowercase) whenever the row is known — or assumed, for `NULL`/undetected rows — not to be Russian.

---

## Background and Motivation

### Why this is safe to change without a big redesign

Empirically (verified by running `pymorphy3.MorphAnalyzer()` directly against sample tokens), pymorphy3 already has an internal fallback chain: for a word it can't find in the Russian dictionary, several no-op guessers run and fail quickly, and a `LatinAnalyzer` finally matches pure Latin-script tokens — including accented Spanish (`canción`, `niño`, `también`) — producing tag `LATN` with `normal_form == word.lower()`. In other words, **for clean en/es tokens, today's code already reduces to a lowercase no-op**; it just gets there by paying for a full pymorphy3 dictionary/guesser pass on every single token, and by trusting pymorphy3's own script-based heuristic instead of the row's actual detected language.

The risk this spec closes is narrower than "every en/es lemma might be wrong": it's tokens that don't cleanly match the Latin-only fallback — chiefly mixed-script OCR garbage (Cyrillic/Latin homoglyph contamination), which `rules/normalize.py` already has code acknowledging as a real OCR artifact class (the `_SUBREDDIT_OCR_RE` and trailing-doubled-letter workarounds exist for exactly this kind of noise). Those tokens can fall past `LatinAnalyzer` into real Russian-suffix-based guessing and produce a nonsensical "lemma." Gating on the row's already-known language removes that risk directly, and as a side effect avoids running the full pymorphy3 pipeline on the ~2/3 of OCR rows that are never Russian.

### Where the two bugs live

1. **`batch/build_bow.py::_build_ocr_bow`** (lines ~153-180): iterates OCR rows, already has `language` in scope (`lang = language or "unknown"`, used to key the output dict), but calls `lemmatize_word(word, morph)` with no language argument for every token regardless of `lang`.
2. **`rules/concept_tagger.py::ConceptTagger.tag()`**: calls `normalize(text, self._morph)` (twice — once at `min_length=3`, once at `min_length=1` for phrase matching) with no language parameter at all. Its only production caller, `batch/build_tags_from_ocr.py`, doesn't currently select `OCRText.language` from the DB — only `text`, `confidence`, `lang_score` — so the language signal isn't even plumbed that far yet.

### What's explicitly NOT part of this bug (confirmed, not assumed)

- `rules/engine.py`'s `lemmatize=True` path (`RulesEngine(..., lemmatize=True)`) is **dead in production**: `settings.RULES.LEMMATIZE` is referenced only in test fixtures (`batch/tests/test_env_loading.py`, `Backend/tests/test_config_integration.py`), never read by any real call site. `batch/build_tags_from_descriptions.py` constructs `RulesEngine(settings.get("RULES.FILE"))` (no `lemmatize` kwarg → defaults `False`) and calls the non-lemmatized `get_tags_for_text()`. This spec makes no changes to `rules/engine.py`.
- `ConceptTagger.tag()` is called only from batch/dev-tooling contexts (`build_tags_from_ocr.py`, `batch/eval_rules.py`, `batch/diff_rules.py`, `batch/tools/spot_check_*.py`, `tests/rules/test_concept_tagger.py`) — never from a live search/query path. No caller exists today that would need a language signal it structurally cannot have.
- Concept/rules **vocabulary** loading (`ConceptTagger._load_concepts`, `build_bow.py::_build_vocab_lemma_set` / `_load_ignore_lemmas`) lemmatizes hand-curated YAML/JSON words, not OCR text, and has no per-word language field. These call sites are unaffected by this spec — see Design Decisions.
- `batch/build_bow.py::_build_descriptions_bow` lemmatizes Ollama-generated image descriptions, which have no per-row language column at all. Unaffected — see Design Decisions.

---

## Design Decisions

| Question | Decision |
|---|---|
| Where does the gating logic live? | **Centralized in `rules/normalize.py`**, not duplicated per caller. `lemmatize_word()` and `normalize()` gain an optional `language: str | None = None` parameter. This matches CLAUDE.md's existing framing of `rules/normalize.py` as the module shared across `build_bow.py` and both rules engines "to keep behavior consistent" — putting the policy in one place is what prevents this exact kind of drift from recurring. |
| What does `language=None` (the default) mean? | **Legacy behavior, unchanged.** Always call `morph.parse()`, letting pymorphy3's own script-based fallback decide (real RU dictionary for Cyrillic, `LatinAnalyzer` passthrough-lowercase for Latin script). This is the escape hatch that keeps every call site with no per-word language signal — concept/rules vocabulary loading, dev tools (`spot_check_*.py`, `eval_rules.py`, `diff_rules.py`), and existing unit tests — working with zero changes required. |
| What does a non-`None` `language` value do? | If it's in `LEMMATIZABLE_LANGUAGES` (`{"ru"}`), lemmatize normally. Otherwise (`"en"`, `"es"`, or the literal string `"unknown"` — anything not `"ru"`), **skip `morph.parse()` entirely** and return `word.lower()`. |
| `NULL`/undetected `language` on an OCR row | Treated as **non-Russian**, not as "no info" (i.e. call sites must pass the string `"unknown"`, never bare `None`, for these rows) — lowercase-only, no pymorphy3 call. This is the conservative choice: never let an unclassified row reach the Russian analyzer on the chance it might be Russian. `build_bow.py` already computes `lang = language or "unknown"` for its output-bucketing; that same value is reused as the `language` argument, so no new fallback logic is needed there. `build_tags_from_ocr.py` applies the equivalent `language or "unknown"` when calling `ConceptTagger.tag()`. |
| Should concept/rules vocabulary loading also be gated? | **No — left unchanged**, deliberately. Vocabulary words (concepts YAML, rules JSON keys, ignore-list) are hand-curated, not OCR'd, so they don't carry the mixed-script/garbled-OCR noise this spec targets, and they have no per-word language field to gate on. Letting pymorphy3's own script-detection (`language=None`) continue to decide per word is the right behavior there — an English band name lemmatizes to `word.lower()` via `LatinAnalyzer` exactly as it does today, and a Russian word still gets real Russian lemmatization. Gating this side would require inventing a language label for hand-written config with no benefit. |
| Should `_build_descriptions_bow` be gated? | **No — out of scope.** `ImageDescription` rows have no `language` column (Ollama descriptions are presumed English-only across environments, and the existing schema reflects that). Adding a language dimension to descriptions is a separate, larger change (new column, backfill, extraction-time detection) that isn't needed to fix the described inconsistency. Left calling `lemmatize_word(word, morph)` with the default `language=None`, i.e. unchanged behavior. |
| Extensibility for Ukrainian / future languages | `LEMMATIZABLE_LANGUAGES` is a module-level `frozenset` constant in `rules/normalize.py`, not a hardcoded `== "ru"` check inline at each call site. If `pymorphy3-dicts-uk` is ever installed, adding `"uk"` to the set is a one-line change that automatically applies everywhere the gate is used. |
| `ConceptTagger.tag()` signature | Add `language: str | None = None` (same default-preserves-legacy-behavior convention as `normalize()`), threaded into both of its internal `normalize()` calls. Optional, not required, so every existing caller without a language signal (dev tools, `eval_rules.py`, `diff_rules.py`, unit tests) compiles and behaves identically without modification. |
| Should `ImagesRepository.get_images_and_ocr_texts()` / `get_images_and_ocr_texts_without_tags()` be modified in place to add `language`? | **No.** Both methods are shared by more than just `build_tags_from_ocr.py`: `batch/diff_rules.py::_load_corpus`, seven `batch/tools/spot_check_*.py` scripts, and two existing integration tests in `tests/integration/test_images_repository.py` all destructure each row as a 5-tuple (`fn, iid, text, conf, _` or equivalent). Inserting a 6th column would break every one of them with `ValueError: too many values to unpack` — verified by grepping every call site, not assumed. Two new methods are added alongside the originals instead (see below); the originals are untouched, so none of those nine call sites or two tests need to change. |

---

## Changes to Existing Components

### `rules/normalize.py`

```python
import re

import pymorphy3

_SUBREDDIT_OCR_RE = re.compile(r'^ri([a-zA-Z]{5,})$', re.IGNORECASE)

# Languages pymorphy3 can meaningfully lemmatize — only Russian dictionaries are
# installed (pymorphy3-dicts-ru). There is no pymorphy3-dicts-uk in any
# requirements file today; add "uk" here if that ever changes.
LEMMATIZABLE_LANGUAGES = frozenset({"ru"})


def make_morph() -> pymorphy3.MorphAnalyzer:
    return pymorphy3.MorphAnalyzer()


def lemmatize_word(word: str, morph: pymorphy3.MorphAnalyzer, language: str | None = None) -> str:
    """
    language=None (default): unchanged legacy behavior — always call
    morph.parse(), relying on pymorphy3's own script-based fallback (real RU
    dictionary lookup for Cyrillic, LatinAnalyzer passthrough-lowercase for
    Latin script). Used by callers with no per-word language signal (concept/
    rules vocabulary loading, dev tools, tests).

    language is a string not in LEMMATIZABLE_LANGUAGES (including "unknown"
    for NULL/undetected OCR rows): pymorphy3 is skipped entirely; returns
    word.lower(). Rows known, or assumed, not to be Russian never reach an
    analyzer that was never designed for them.

    language in LEMMATIZABLE_LANGUAGES ("ru"): real pymorphy3 lemmatization.

    Note for callers outside this module (e.g. trends_batch's separate
    lemmatize_phrase, see the sibling trends-lemmatization spec): the
    None-means-"run pymorphy3 anyway" default here is a per-call fallback for
    callers with no language signal at all. A caller that already knows its
    own language ahead of time (trends_batch checks `language == "ru"` before
    ever calling into lemmatization) doesn't rely on this default — it simply
    never calls this function for non-Russian content in the first place.
    """
    if language is not None and language not in LEMMATIZABLE_LANGUAGES:
        return word.lower()
    parsed = morph.parse(word)
    return parsed[0].normal_form if parsed else word.lower()


def tokenize(text: str) -> list[str]:
    # unchanged
    return re.findall(r'[^\W_]+', text, re.UNICODE)


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
        m = _SUBREDDIT_OCR_RE.match(word)
        if m:
            suffix = m.group(1)
            if len(suffix) >= min_length:
                result.add(lemmatize_word(suffix, morph, language))
        if len(word) > min_length + 1 and word[-1].isalpha() and word[-1] == word[-2]:
            shorter = word[:-2]
            if len(shorter) >= min_length:
                result.add(lemmatize_word(shorter, morph, language))
    return result
```

Only the function signatures and the one new `if` in `lemmatize_word` change; `tokenize()` and the rest of `normalize()`'s body are untouched.

### `batch/build_bow.py::_build_ocr_bow`

```python
for text, confidence, language, lang_score in rows:
    metrics.increment("ocr.rows.total")
    if not passes_language_filter(confidence, lang_score, confidence_min, lang_score_min):
        if confidence is not None and confidence < confidence_min:
            metrics.increment("ocr.rows.skipped.low_confidence")
        else:
            metrics.increment("ocr.rows.skipped.low_lang_score")
        continue
    lang = language or "unknown"
    for word in tokenize(text):
        if len(word) < min_word_length or word.isdigit():
            continue
        lang_counters[lang][lemmatize_word(word, morph, lang)] += 1
    metrics.increment("ocr.rows.processed")
```

Single-line change: `lemmatize_word(word, morph)` → `lemmatize_word(word, morph, lang)`. `lang` is already computed (`language or "unknown"`) and already used to key `lang_counters`, so this reuses an existing variable rather than introducing a new one. No changes to `_build_descriptions_bow`, `_load_ignore_lemmas`, `_build_vocab_lemma_set`, `_build_json_rules_lemma_set`, or `_build_concepts_lemma_set` — see Design Decisions.

### `rules/concept_tagger.py::ConceptTagger.tag`

```python
def tag(self, text: str, language: str | None = None) -> TagResult:
    lemma_bag = normalize(text, self._morph, language=language)
    # Phrases may contain short words ("zz" in "zz top", "in" in "alice in chains").
    # normalize() drops them, making phrase checks impossible. Build a separate full
    # bag with no length filter just for phrase matching.
    lemma_bag_full = normalize(text, self._morph, min_length=1, language=language)
    ...  # unchanged below this point
```

No changes to `ConceptTagger.load`, `_load_tags`, or `_load_concepts` (vocabulary loading — see Design Decisions).

### `repository/images.py::ImagesRepository`

**Do not modify `get_images_and_ocr_texts()` / `get_images_and_ocr_texts_without_tags()` in place.** Both are shared by callers outside this spec's scope that destructure rows as 5-tuples and would break if a column were inserted into the existing SELECT — confirmed by grep, not assumed:

- `batch/diff_rules.py::_load_corpus`: `[(fn, iid, text) for fn, iid, text, conf, _ in rows ...]`
- `batch/tools/spot_check_band.py`, `spot_check_burzum.py`, `spot_check_losses.py`, `spot_check_mem.py`, `spot_check_metal_losses.py`, `spot_check_metallica.py`, `spot_check_slayer.py` — same 5-tuple unpack pattern
- `tests/integration/test_images_repository.py::test_get_images_and_ocr_texts_includes_lang_score` and `::test_get_images_and_ocr_texts_without_tags_includes_lang_score` — explicitly assert the row shape is `(filename, img_id, txt, confidence, lang_score)`

None of these need `language` — they're all out of scope per the Design Decisions table above (dev tools with no language signal to use it for). Instead, add two **new** methods that include the extra column, leaving the originals byte-for-byte unchanged:

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

`self.ocr.language` is inserted before `lang_score` to match column order in `Storage/models.py::OCRText`; `build_tags_from_ocr.py` unpacks by position, so the order matters and is called out explicitly here. `get_images_and_ocr_texts` / `get_images_and_ocr_texts_without_tags` (original, no-language versions) and `get_images_and_descriptions[_without_tags]` are all untouched — the latter has no `language` column on `ImageDescription` to add in the first place.

### `batch/build_tags_from_ocr.py`

```python
if incremental:
    images_and_texts_results = await images_repo.get_images_and_ocr_texts_without_tags_with_language("OCR")
else:
    images_and_texts_results = await images_repo.get_images_and_ocr_texts_with_language()

...

for filename, image_id, text, confidence, language, lang_score in images_and_texts_results:
    if not passes_language_filter(confidence, lang_score, ocr_confidence_min, ocr_lang_score_min):
        metrics.increment("images.skipped")
        tracker.skip()
        continue
    result = engine.tag(text, language=language or "unknown")
    tag_count = len(result.tags)
    for tag_name, tag_value in result.tags:
        tags_saver.add_tag(image_id, tag_name, tag_value, "OCR")
    metrics.increment("images.processed")
    metrics.add("tags.total", tag_count)
    metrics.bucket("tags_per_image", tag_count)
    tracker.mark_done()
```

Three changes: the two repository calls switch to the new `_with_language` methods, the tuple unpack gains `language`, and `engine.tag(text)` becomes `engine.tag(text, language=language or "unknown")`.

---

## Non-Goals

- No new dependencies, and no real per-language lemmatizer for English or Spanish (e.g. spaCy). The empirical finding above shows today's en/es behavior already reduces to a lowercase no-op for clean tokens — this spec closes the risk gap for garbled/mixed-script tokens and removes reliance on pymorphy3's own guessing for languages it was never built for, but it does not add "real" English/Spanish lemmatization (`running`/`run` stay distinct lemmas, exactly as today).
- No changes to `rules/engine.py` — its `lemmatize=True` path is confirmed dead in production (see Background).
- No changes to concept/rules vocabulary loading (`_load_concepts`, `_build_vocab_lemma_set`, `_load_ignore_lemmas`, `_build_json_rules_lemma_set`, `_build_concepts_lemma_set`) — see Design Decisions.
- No changes to `_build_descriptions_bow` or the `ImageDescription` schema — see Design Decisions.
- No changes to `batch/build_lemma_clusters.py` or `batch/draft_concepts_from_clusters.py` — neither performs lemmatization itself; both consume `build_bow.py`'s already-lemmatized output and are unaffected once that output improves.
- No update to `batch/eval_rules.py`'s or `batch/diff_rules.py`'s golden sets to add per-item language labels, and no changes to `batch/tools/spot_check_*.py`. These call `ConceptTagger.tag(text)` with no language, which is unaffected (`language=None` preserves legacy behavior) — adding language labels to golden sets to exercise the new gating explicitly would be a reasonable, separate follow-up, not required to fix this bug.
- No data migration. This is a pure change to how lemmas are *computed* at read/batch-processing time — `ocr_texts.language`/`lang_score` values themselves are untouched.

---

## Error Handling and Edge Cases

| Case | Behavior |
|---|---|
| Row `language` is `NULL` (legacy rows, or undetected) | `build_bow.py` and `build_tags_from_ocr.py` both already/newly compute `language or "unknown"` before calling into the lemmatizer — `"unknown"` is not in `LEMMATIZABLE_LANGUAGES`, so these rows get lowercase-only, never reach pymorphy3's Russian dictionary. |
| Row `language == "ru"` | Unchanged — full pymorphy3 lemmatization, as today. |
| Row `language` is `"en"` or `"es"` | New: lowercase-only, no `morph.parse()` call. For clean tokens this is numerically identical to today's `LatinAnalyzer` fallback output (see Background); for mixed-script/garbled tokens it now avoids the Russian-suffix-guesser misfire this spec exists to prevent. |
| Concept/rules vocabulary word lemmatization (`_load_concepts`, `_build_vocab_lemma_set`, etc.) | Unaffected — these call sites don't pass `language`, so `lemmatize_word`/`normalize` default to `language=None` (today's behavior, unchanged). |
| `ConceptTagger.tag(text)` called with no `language` (dev tools, `eval_rules.py`, `diff_rules.py`, existing unit tests) | Unaffected — `language=None` default preserves today's behavior exactly; no caller update required for these to keep working. |
| `_build_descriptions_bow` | Unaffected — no `language` argument passed, defaults to `None`, unchanged behavior (see Non-Goals). |

---

## Testing Plan

- **`tests/rules/test_normalize.py` (new file — none exists today):** unit tests for `lemmatize_word()` and `normalize()`'s new `language` parameter:
  - `language=None` reproduces today's behavior for both a Russian word and a Latin word (regression guard against accidentally changing the default path).
  - `language="ru"` still performs real Russian lemmatization (e.g. a plural/inflected Russian word collapses to its dictionary base form).
  - `language="en"` / `language="es"` / `language="unknown"` all return `word.lower()` for a Latin-script word — verify deterministically by spying on `morph.parse` (e.g. `unittest.mock.Mock(wraps=morph)`) and asserting it is never called for these three cases, rather than relying on a specific word that happens to trip pymorphy3's guesser (fragile — depends on undocumented internals that could change with a pymorphy3 upgrade).
- **`tests/rules/test_concept_tagger.py`:** add cases calling `engine.tag(text, language=...)` for `"en"`/`"es"`/`"ru"`/`"unknown"`, confirming vocabulary matching (word/phrase/fuzzy) still fires correctly for each, and that an existing no-`language`-arg test still passes unmodified (backward-compat check).
- **`tests/integration/test_build_ocr_bow_lang_filter.py`:** extend with a row where `language="es"` and text contains a token that would be mis-lemmatized if it reached pymorphy3's Russian guesser (e.g. a mixed-script or edge-case token), asserting the output lemma is the plain lowercased token.
- **`tests/integration/test_images_repository.py`:** add new tests for `get_images_and_ocr_texts_with_language` / `get_images_and_ocr_texts_without_tags_with_language` (mirroring the existing `..._includes_lang_score` tests but asserting the 6-tuple shape and that `language` comes back correctly). The two *existing* tests in this file are not touched and must keep passing unmodified — they exercise the original, untouched methods.
- Standard pre-commit gate for this change: `cd Backend && pytest`, `pytest tests/rules/`, `pytest batch/tests/` (per CLAUDE.md); the OCR-lang-filter integration test additionally requires the live Postgres integration fixture already used by `tests/integration/`.

---

## Implementation Order

1. `rules/normalize.py` — add `LEMMATIZABLE_LANGUAGES`, extend `lemmatize_word()`/`normalize()` with the optional `language` parameter. Add `tests/rules/test_normalize.py`.
2. `batch/build_bow.py::_build_ocr_bow` — pass `lang` into `lemmatize_word`.
3. `rules/concept_tagger.py::ConceptTagger.tag` — add `language` parameter, thread into both `normalize()` calls. Extend `tests/rules/test_concept_tagger.py`.
4. `repository/images.py` — add the two new `_with_language` methods (originals untouched). Add their integration tests.
5. `batch/build_tags_from_ocr.py` — switch to the new repository methods, unpack the new `language` column, pass `language=language or "unknown"` into `engine.tag()`.
6. Extend `tests/integration/test_build_ocr_bow_lang_filter.py` with a language-gating case.
7. Re-run `build_bow` and `build_tags_from_ocr` (full rebuild, not `--incremental` — this is a lemmatization-logic change, not new data) on each environment; spot-check that `unmatched.<env>.json`'s `en`/`es` blocks no longer contain any lemma that looks like a Russian-suffix-guessed artifact.

---

## Side Notes

- CLAUDE.md previously described `rules/concept_tagger.py` as "Not yet wired into the main pipeline," despite `batch/build_tags_from_ocr.py` already importing and using `ConceptTagger` directly. This has since been corrected in CLAUDE.md (fixed separately, outside this spec).
- If a future need arises for genuine English/Spanish lemmatization (not just gating out the Russian analyzer), the `language` parameter added here is the natural extension point — the "Approach C" alternative considered during brainstorming (a pluggable per-language lemmatizer registry) becomes easy to layer in later without another redesign, once/if that's actually needed.
