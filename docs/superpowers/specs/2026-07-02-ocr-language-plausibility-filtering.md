# OCR Language Plausibility Filtering

Status: done

**Date:** 2026-07-02
**Scope:** `Storage/models.py`, new Alembic migration, `repository/ocr_text.py`, `batch/extract_text_from_memes.py`, new `batch/lang_plausibility.py`, new `batch/score_ocr_language.py`, `batch/build_bow.py`, `batch/build_tags_from_ocr.py`, new `batch/eval_ocr_language_filter.py`, new `batch/data/tagging/golden_ocr_language.yaml`

---

## Summary

`extract_text_from_memes` runs three independent EasyOCR readers (`ru`, `en`, `es`) on every image, unconditionally, and persists every reader's output to `ocr_texts`. When an image's actual text is in one language, the other two readers still produce output — because each reader is constrained to its own character set, it doesn't fail cleanly, it emits a plausible-looking but wrong guess in its own alphabet. That garbage propagates downstream into `build_bow`'s per-language word lists and — more seriously — directly into `build_tags_from_ocr`'s tag derivation, which today applies no language check at all.

This spec adds a per-OCR-row **language plausibility score**, computed from word-frequency lookups (`wordfreq`) against each row's assigned reader-language, stored alongside the existing `confidence` field, and used to filter noise downstream — without discarding raw OCR data and without restructuring the extraction pipeline.

---

## Background and Motivation

### Why this happens

EasyOCR readers are initialized per-language (`easyocr.Reader(['ru'])`, `easyocr.Reader(['en'])`, `easyocr.Reader(['es'])`), each with a fixed recognition character set. On a Cyrillic-only meme:

- The `ru` reader correctly recognizes Cyrillic text.
- The `en`/`es` readers **cannot output Cyrillic at all** — their models can only choose among Latin glyphs. Faced with Cyrillic strokes, they don't refuse; they pick the closest-looking Latin letter sequence and report it, sometimes with non-trivial confidence.

This means a naive Unicode-script check (is the output Cyrillic vs Latin?) does not solve the problem: the `en`/`es` misreads are already valid Latin script by construction. The distinguishing signal is not *script*, it's *whether the recognized string consists of real words in the language it claims to be*.

### Where the garbage currently goes unfiltered

- `build_bow.py` (`_build_ocr_bow`) buckets tokens by `ocr_texts.language` and only filters by `confidence < OCR_CONFIDENCE_MIN` (default `0.4`). A wrong-language row with confidence ≥ 0.4 pollutes that language's word-frequency table.
- `build_tags_from_ocr.py` filters by `confidence < OCR_CONFIDENCE_MIN` only — it does not consult `language` at all. Garbage rows are tagged through the concept rules engine exactly like genuine text, directly corrupting the OCR-derived tag set.
- `repository/ocr_text.py::overwrite_texts` has a standing `# todo: threshold confidence` comment acknowledging this gap was never closed.

Confidence alone is not a sufficient filter: OCR confidence reflects *character-shape* certainty, not *linguistic* plausibility. A wrong-language misread can have deceptively high confidence (clean, sharp glyphs that just happen to resemble Latin letters), while genuine text in noisy fonts can have low confidence. The two signals are complementary, not substitutes.

---

## Design Decisions

| Question | Decision |
|---|---|
| Granularity: per-image or per-OCR-row? | **Per-row.** Memes frequently mix languages (e.g. a Russian meme with an English watermark or vice versa); a per-image verdict would force keep/drop of an entire image's text at once and lose genuine minority-language rows. Per-row matches the existing data model (`ocr_texts` is already one row per detected text region per reader-language) and requires no new aggregation concept. A per-image *aggregate* signal was considered (roll up row scores into a per-image dominant-language field, to help disambiguate short/ambiguous rows) — **deferred**, not part of this spec. It adds a second moving part for a benefit that's unproven until row-level scoring is measured against the golden set; if the eval (see Metric) shows short-row disambiguation is a real problem, it can be layered on later using the same `ocr_texts` data, no schema rework needed. |
| Detection method | **Lexical plausibility via word-frequency lookup (`wordfreq` library)**, not a general-purpose language identifier. Rejected alternatives: (a) statistical LID (`langdetect`, `fasttext lid.176`) — unreliable on short strings (meme text is typically 1–6 words per bbox), and confidently misclassifies gibberish; (b) Unicode script-ratio check — doesn't apply to this failure mode at all (see Background), since `en`/`es` misreads are already Latin script. `wordfreq` gives, per token, a per-language "is this a word real people use" signal (Zipf frequency, 0 = unknown) and already ships frequency data for `en`, `es`, and `ru` — no per-language special-casing needed, and no GPU/network dependency. |
| Score is destructive or additive? | **Additive.** Compute and store a `lang_score` per row; never delete or skip persisting rows at extraction time. OCR (GPU-bound, slow) and scoring (CPU, cheap, deterministic given `text`) are decoupled — the threshold can be retuned, or the scoring method swapped, without re-running OCR. Filtering happens downstream, mirroring the existing `OCR_CONFIDENCE_MIN` pattern already used by `build_bow.py` and `build_tags_from_ocr.py`. |
| Where is the score computed? | At write time in `extract_text_from_memes` (so new data is scored going in), **and** via a standalone backfill script for existing rows (`batch/score_ocr_language.py`), since the column doesn't exist for already-extracted data. Both paths call the same `batch/lang_plausibility.py::score(text, language)` function — no duplicated logic. |
| Short / non-alphabetic rows | Rows with fewer than 2 alphabetic tokens (numbers, emoji-only, single short words) get `lang_score = NULL` — "not scored," not "scored zero." Downstream filters treat `NULL` as pass-through (fall back to `confidence` alone), since `wordfreq` lookups are unreliable at that length and a false "garbage" verdict on a short row is exactly the over-suppression failure mode this spec exists to avoid, not introduce. |
| Threshold enforcement point | New `OCR_LANG_SCORE_MIN` env var (default `0.3`), consumed the same way `OCR_CONFIDENCE_MIN` already is, in both `build_bow.py` and `build_tags_from_ocr.py` (the latter currently has no language filtering at all — this is also a bug fix). Extraction-time write is unaffected; nothing is deleted from `ocr_texts` by this spec. |

---

## Scoring Function

### `batch/lang_plausibility.py`

```python
from wordfreq import zipf_frequency
from rules.normalize import tokenize

_MIN_ALPHA_TOKENS = 2
_ZIPF_KNOWN_THRESHOLD = 1.0  # below this, wordfreq effectively hasn't seen the word


def score(text: str, language: str) -> float | None:
    """
    Fraction of alphabetic tokens in `text` that are recognized words in
    `language`, per wordfreq's frequency data. Returns None if there are
    fewer than _MIN_ALPHA_TOKENS alphabetic tokens to judge from (too
    short/noisy to score reliably) rather than guessing.
    """
    tokens = [t for t in tokenize(text) if not t.isdigit()]
    if len(tokens) < _MIN_ALPHA_TOKENS:
        return None

    known = sum(1 for t in tokens if zipf_frequency(t, language) >= _ZIPF_KNOWN_THRESHOLD)
    return known / len(tokens)
```

- Reuses `rules.normalize.tokenize` (already the shared tokenizer for `build_bow`, `concept_tagger`, and `normalize`) rather than introducing a second tokenization rule.
- `wordfreq` language codes match the reader languages used already (`en`, `es`, `ru`) — no mapping table needed.
- Pure function, no I/O, trivially unit-testable and reusable by both the live extraction path and the backfill script.

---

## Data Model Change

`Storage/models.py`, `OCRText`:

```python
lang_score = Column(Float, nullable=True)  # None = not scored (too short); else 0.0–1.0
```

New Alembic migration (autogenerate from `Storage/`, per existing workflow):

```powershell
alembic revision --autogenerate -m "add lang_score to ocr_texts"
```

No backfill happens automatically as part of the migration — `lang_score` starts `NULL` for all existing rows; `batch/score_ocr_language.py` populates it explicitly (see below). This keeps the schema migration itself fast and reversible.

---

## Changes to Existing Components

### `repository/ocr_text.py::overwrite_texts`

Compute `lang_score` inline when building each row, using the language already passed to the method (the reader's assigned language — exactly the value `score()` needs):

```python
from batch.lang_plausibility import score as lang_score

...
for bbox, text, confidence in ocr_result:
    self.session.add(
        OCRText(
            image_id=image.id,
            text=text,
            confidence=float(confidence),
            bbox=[...],
            language=language,
            lang_score=lang_score(text, language),
        )
    )
```

No change to `extract_text_from_memes.py` itself — the reader loop already passes `language` into `committer.add_language_result`, which is unchanged; the scoring is fully contained in the repository.

### `batch/build_bow.py::_build_ocr_bow`

Add a second threshold check alongside the existing confidence check:

```python
OCR_LANG_SCORE_MIN = float(os.getenv("OCR_LANG_SCORE_MIN", "0.3"))

...
for text, confidence, language, lang_score in rows:
    metrics.increment("ocr.rows.total")
    if confidence is not None and confidence < confidence_min:
        metrics.increment("ocr.rows.skipped.low_confidence")
        continue
    if lang_score is not None and lang_score < OCR_LANG_SCORE_MIN:
        metrics.increment("ocr.rows.skipped.low_lang_score")
        continue
    ...
```

Requires extending `OCRTextRepository.get_all_texts_with_language` to also select `OCRText.lang_score` (rename or add a sibling method — see Reuse below).

### `batch/build_tags_from_ocr.py`

Currently has no language-plausibility filtering at all (see Background). Add the same threshold check next to the existing confidence check:

```python
OCR_LANG_SCORE_MIN = float(os.getenv("OCR_LANG_SCORE_MIN", "0.3"))

...
for filename, image_id, text, confidence, lang_score in images_and_texts_results:
    if confidence < OCR_CONFIDENCE_MIN:
        metrics.increment("images.skipped")
        continue
    if lang_score is not None and lang_score < OCR_LANG_SCORE_MIN:
        metrics.increment("images.skipped.low_lang_score")
        continue
    ...
```

Requires extending `ImagesRepository.get_images_and_ocr_texts` (and `..._without_tags`) to also select `OCRText.lang_score`.

### `batch/score_ocr_language.py` (new)

Backfill script, following the existing maintenance-script pattern (`reset_ocr_status.py`, `deduplicate_ocr_texts.py`): loads all `ocr_texts` rows with `lang_score IS NULL`, computes `score(text, language)`, writes it back in batches (reuse `BatchCommitter`-style commit chunking). Idempotent — re-running only touches unscored rows, safe to run repeatedly as new rows land without `lang_score` from older code, or after `_ZIPF_KNOWN_THRESHOLD` tuning (accepts `--rescore-all` to force recompute for every row, for when the threshold constant itself changes).

```
python -m batch.score_ocr_language            # scores rows where lang_score IS NULL
python -m batch.score_ocr_language --rescore-all
```

---

## Metric: Garbage-Filtering Quality

Mirrors the existing golden-set eval pattern in `batch/eval_rules.py` (per-tag precision/recall/F1 against a hand-labeled YAML set).

### Golden set: `batch/data/tagging/golden_ocr_language.yaml`

A hand-labeled sample of real `ocr_texts` rows, stratified across:
- Genuine text in each of `ru`/`en`/`es` (should NOT be flagged garbage).
- Cross-language misreads for each reader (e.g. `en` reader output on a `ru`-only image) — the exact failure mode this spec targets (SHOULD be flagged garbage).
- Short/ambiguous rows (numbers, single words, emoji) — to verify the `NULL`-score pass-through behaves sanely rather than silently miscounted.

Target ~250–300 rows total, pulled by manually reviewing a stratified sample of production `ocr_texts` (e.g. rows from images where a `ru` row exists with high confidence, cross-referenced against that same image's `en`/`es` rows as garbage candidates for labeling).

```yaml
- text: "СТАРТ ЗДЕСЬ"
  language: ru
  is_garbage: false
- text: "CTAPT 3ДECb"     # en reader's Latin-glyph misread of the same Cyrillic text
  language: en
  is_garbage: true
- text: "lol"
  language: en
  is_garbage: false
```

### `batch/eval_ocr_language_filter.py` (new)

```
python -m batch.eval_ocr_language_filter --golden batch/data/tagging/golden_ocr_language.yaml [--threshold 0.3]
```

For each golden row, compute `score(text, language)`, apply the threshold, compare to the `is_garbage` label. Report:

- **Precision / Recall / F1** for the "flagged as garbage" classification — standard signal, consistent with `eval_rules.py`'s existing output shape.
- **False-suppression rate** (= false positives / total genuine rows) as a **headline metric, reported separately and first** — a genuine row wrongly flagged as garbage is the costlier mistake (it silently deletes real signal from tags/search), so it's tracked distinctly rather than folded into a single aggregate score where it could be masked by high recall on the (comparatively low-stakes) garbage side.
- A **threshold sweep table** (score cutoffs, e.g. 0.1 through 0.6 in steps of 0.05, vs precision/recall/false-suppression at each) to support picking `OCR_LANG_SCORE_MIN` deliberately rather than guessing.

This script is the tool used to choose and justify the `OCR_LANG_SCORE_MIN` default, and to catch regressions if `_ZIPF_KNOWN_THRESHOLD` or the tokenizer changes later.

---

## Non-Goals

- No change to which readers run per image, or when — all three readers (`ru`/`en`/`es`) still run unconditionally on every image. This spec is about post-hoc quality filtering, not compute reduction or pre-routing. (A pre-routing approach was considered and rejected: it risks missing genuine secondary-language text in mixed-language memes, and compute cost was not identified as a driver for this work.)
- No per-image aggregate/dominant-language field (see Design Decisions) — deferred pending evidence from the golden-set eval that row-level scoring alone is insufficient.
- No deletion of existing `ocr_texts` rows. Filtering is applied downstream at read time; nothing in this spec removes data from the table.
- No change to the `ru`/`en`/`es` reader set, EasyOCR models, or `ocr_preprocess.py` variant generation.
- No new statistical language-ID model or dependency beyond `wordfreq` (pure-Python, no GPU/network requirement).

---

## Dependencies

- `wordfreq` — new addition to `requirements.txt` (batch/ML stack). Pure-Python, ships static frequency word lists for `en`/`es`/`ru` (and many other languages), no GPU, no network access at runtime.

---

## Error Handling and Edge Cases

| Case | Behaviour |
|---|---|
| Text with < 2 alphabetic tokens (numbers, emoji, single short word) | `lang_score = NULL` — not scored, treated as pass-through by downstream filters (fall back to `confidence` alone) |
| `wordfreq` has no data for a language code | Not expected — `en`/`es`/`ru` are all covered by `wordfreq`'s core data; if a new reader language is ever added without `wordfreq` support, `score()` should raise clearly at import/startup rather than silently returning `None` for every row (fail fast, don't silently disable filtering) |
| `lang_score` is `NULL` in downstream filters | Treated as "no verdict" — row passes the language-score check, confidence check still applies |
| Backfill run (`score_ocr_language.py`) interrupted mid-run | Idempotent — re-running only processes rows still `NULL`, no duplicate work, no data loss (matches the `reset_ocr_status` / `extract_text_from_memes` full-rerun pattern already documented in `CLAUDE.md`) |
| `--rescore-all` used after changing `_ZIPF_KNOWN_THRESHOLD` | Recomputes every row from stored `text`/`language` — no OCR re-run needed |

---

## Implementation Order

1. `batch/lang_plausibility.py` — standalone `score()` function, unit tests against known real/garbage strings in `ru`/`en`/`es`.
2. Add `wordfreq` to `requirements.txt`.
3. Alembic migration: add `lang_score` column to `ocr_texts`.
4. Wire `score()` into `repository/ocr_text.py::overwrite_texts` (new rows scored going forward).
5. `batch/score_ocr_language.py` — backfill script for existing rows.
6. Build the golden set (`golden_ocr_language.yaml`) from a manual review of production data.
7. `batch/eval_ocr_language_filter.py` — run against the golden set, use the threshold sweep to pick `OCR_LANG_SCORE_MIN`.
8. Wire `OCR_LANG_SCORE_MIN` filtering into `build_bow.py` and `build_tags_from_ocr.py` (including the repository query changes each needs).
9. Re-run `build_bow` and `build_tags_from_ocr` (`--incremental` not applicable for a filtering-logic change — full rebuild) on each environment; spot-check that known garbage clusters (e.g. `en`/`es` word lists on RU-heavy environments) shrink.

---

## Side Notes

- `wordfreq`'s frequency lists skew toward general-purpose text (news, web crawl, subtitles) and will legitimately score some meme-specific slang, leetspeak, and proper nouns as "unknown" even when genuine. This is exactly why the metric's headline number is false-suppression rate, not raw accuracy — the golden set and threshold sweep exist specifically to catch and tune around this before `OCR_LANG_SCORE_MIN` is set aggressively. If false-suppression proves too high even at a lenient threshold, the fallback is to widen `_MIN_ALPHA_TOKENS` or lower `_ZIPF_KNOWN_THRESHOLD` rather than abandoning the approach — the golden set makes that a measurable decision instead of a guess.
- `rules/normalize.py::tokenize` is reused as-is for consistency with the rest of the pipeline (`build_bow`, `concept_tagger`), not reimplemented — `lang_plausibility.py` imports it directly.