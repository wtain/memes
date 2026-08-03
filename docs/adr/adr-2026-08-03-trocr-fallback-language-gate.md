# ADR 2026-08-03: TrOCR fallback — model fix and language-plausibility gate

STATUS: ACCEPTED

Related: `docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md`
(origin of `rules.lang_plausibility.score()`, reused here)

## Context

`batch/trocr_fallback.py::TrOCRFallback` re-recognizes low-confidence EasyOCR
detections (`confidence < 0.5`) using TrOCR, meant for stylized/distorted
English fonts (Lobster, Impact) where EasyOCR gets the bounding box right but
misreads the characters. It has been silently broken since introduction:
`MODEL_ID = "microsoft/trocr-base-scene"` is not a real Hugging Face repo (no
such model exists — the closest real one is `microsoft/trocr-base-str`), so
`TrOCRFallback.__init__` always raised, `extract_text_from_memes.py` caught
the exception and printed "TrOCR unavailable ... skipping fallback", and the
feature was a permanent no-op.

Fixing the model ID alone surfaced a second, worse problem. `extract_text_
from_memes.py` runs three EasyOCR readers (`ru`/`en`/`es`) unconditionally on
every image (see the referenced spec) — on a Cyrillic-only meme, the `en`
reader can't output Cyrillic at all, so it picks the closest-looking Latin
glyphs and reports them, usually with confidence < 0.5. Most low-confidence
`en` detections in this corpus are this failure mode, not font stylization.
TrOCR is English-only and can't recover a wrong-language misread — tested
against a real 40-image sample and found it turning garbled-but-recognizable
Cyrillic transliterations into fluent, confident, **completely unrelated**
English words:

```
EasyOCR: 'AMOXET HA TEBa, CVKAPH'      -> TrOCR: 'AMORETRATECRACY'
EasyOCR: 'ECTb OTJHYHBIi CIOCO6 y3HaTb' -> TrOCR: 'ELECTROENCEPHALOGRAPHS'
```

...then stamping the result with `TROCR_SYNTHETIC_CONFIDENCE = 0.55`, i.e.
*more* trustworthy than the EasyOCR score it replaced. Enabling the fixed
model ID with no further changes would have made OCR quality on this corpus's
substantial Russian-language content actively worse than doing nothing.

## Decision

1. **Model ID**: `microsoft/trocr-base-scene` → `microsoft/trocr-base-str`
   (the real scene-text-recognition model). Verified it downloads and loads.

2. **Gate `rerecognize()` on language plausibility before cropping/running
   anything**, reusing `rules.lang_plausibility.score()` — the same scorer
   `build_bow.py`/`build_tags_from_ocr.py` already use for this exact
   cross-language-misread problem, rather than inventing a second one.
   `TrOCRFallback._is_plausibly_english(text)`: `score(text, "en") is None or
   score(text, "en") >= TROCR_MIN_LANG_SCORE`. `None` (fewer than 2
   alphabetic tokens — the scorer can't judge short text reliably) passes
   through by design: single short words are exactly the case TrOCR is meant
   to help with, and a false "garbage" verdict there would defeat the
   feature entirely.

3. **`TROCR_MIN_LANG_SCORE = 0.6`, a local constant, deliberately not reused
   from `settings.OCR.LANG_SCORE_MIN` (0.3).** That value is tuned via
   `build_bow`/`build_tags_from_ocr`'s own golden-set eval
   (`batch/eval_ocr_language_filter.py`) for "don't lose genuine
   minority-language OCR rows" — a different, more lenient bar than "should
   an English-only model be trusted to rewrite this text". Reusing it here
   without re-running that eval would have silently repurposed an
   already-calibrated threshold for a use case it wasn't validated against.
   Picked 0.6 by measuring against the false-negative score distribution
   from the same 40-image sample (scores 0.33–0.8); 0.6 rejects the clear
   majority.

Net result on that sample: went from **195/195 (100%)** low-confidence
detections sent to TrOCR (the naive model-ID-only fix) to **92/196 (47%)**,
i.e. 104/196 now correctly left as the original EasyOCR text.

## Rejected alternative: token-length filtering

Two known false positives remained at threshold 0.6 (`"Ana MeHa OHa
BbIrnAMMT Tak:"` scored 0.8, `"Ha3blbaeTCa VX BaM-dam"` scored 0.667) —
both caused by short filler-length words ("Ana", "Tak", "VX") coincidentally
matching real English words in `wordfreq`'s frequency data, inflating the
ratio of an otherwise-garbled string. Tried adding a `min_token_len`
parameter to `lang_plausibility.score()` (excluding tokens shorter than 4
chars from consideration) and set `TrOCRFallback` to pass `min_token_len=4`.

In isolation this fixed both targeted cases. Measured against the full
40-image sample, it was a **net regression**: 99/196 sent to TrOCR (worse
than 92/196), because filtering out short tokens also shrinks many other
short 2–3-word garbled phrases below the `_MIN_ALPHA_TOKENS = 2` floor,
flipping them from "scored low, correctly rejected" to "too few tokens to
judge → `None` → passes through unconditionally". 16 previously-correct
rejections broke to fix only 9. Reverted — kept threshold-only tuning
(`TROCR_MIN_LANG_SCORE = 0.6`, no length filter) as the shipped state.

This is the concrete reason every tuning step in this area was validated
against the same real 40-image sample rather than the specific cases being
fixed: a change that looks like a strict improvement on the two known
failures made the aggregate worse.

## Consequences

- OCR quality for Russian-heavy content in this corpus is meaningfully
  better than before this change (TrOCR no longer corrupts most misreads)
  and no worse than before TrOCR existed (it was already a no-op).
- Known, accepted residual gap: multi-short-word garbled strings where
  several tokens happen to coincidentally match real English words (score
  ≥ 0.6) still get sent to TrOCR and corrupted. Not fixed — see Rejected
  Alternative above. `test_min_token_len_catches...` was considered but the
  approach itself was rejected, so no such test exists; the residual gap is
  covered by `test_gate_is_not_perfect_on_short_mixed_strings` in
  `tests/ai/test_trocr_fallback.py`.
- No golden-set/eval script was built for this gate (unlike
  `eval_ocr_language_filter.py` for the shared scorer's own threshold) — the
  40-image sample was a spot check, not a systematic evaluation. If this
  residual gap becomes a real problem (not just a known imperfection), the
  next step would be building a golden set of real low-confidence `en`
  detections (genuine-stylized-English vs wrong-language-misread, labeled)
  and running a threshold/parameter sweep against it — the same rigor
  `docs/superpowers/specs/2026-07-02-ocr-language-plausibility-filtering.md`
  used for the shared scorer — rather than further ad hoc heuristics.
- `rules.lang_plausibility.score()` itself was **not** modified — the
  rejected `min_token_len` parameter never got added, so this file's
  behavior is unchanged for all existing callers.
