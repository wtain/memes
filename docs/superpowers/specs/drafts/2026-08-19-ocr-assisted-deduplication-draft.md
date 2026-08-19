# OCR-Assisted Deduplication — Draft

Status: draft

## Idea

Duplicate detection today is embedding-only: CLIP visual similarity (`image_embeddings` →
`tmp_duplicates` via `rebuild_duplicates.py`'s KNN probe → `clusterize.py`'s union-find). This
causes two classes of error:

1. **False positives**: visually near-identical images (same meme template) with different text
   get clustered as duplicates. This is the problem
   `2026-08-19-duplicate-dismissal-decisions-design.md` addresses *reactively*, after a human
   catches it.
2. **Possibly false negatives**: genuine duplicates that render differently enough (recompression,
   cropping, watermarks, a resized re-upload) that CLIP distance exceeds the clustering threshold,
   while their OCR text would match exactly or near-exactly.

Idea: use OCR text (already extracted per image by `extract_text_from_memes`, already indexed for
smart search by `build_ocr_lemmas`) as a second duplicate-detection signal, either:

- **(a) A filter/booster on the existing embedding signal** — e.g. only treat a candidate pair as a
  confirmed duplicate if OCR similarity is also high; or down-rank/suppress a candidate whose OCR
  text is clearly different, directly reducing false positives at the source instead of relying on
  a human to dismiss them one cluster at a time.
- **(b) An independent candidate-pair signal** — images with near-identical OCR text but weak/no
  embedding match, catching near-duplicates the embedding-only approach currently misses.

## Why this is a separate spec, not folded into the dismissal-decisions work

- OCR-text similarity is a fundamentally different kind of judgment than "a human confirmed these
  two are different." It's algorithmic, has its own tuning surface (similarity threshold, per-
  language handling across EN/ES/RU, how to treat OCR-empty images), and — per the concern that
  prompted this draft — is error-prone enough to need its own validation pass before it touches
  production clustering.
- The dismissal-decisions spec is deliberately a reactive, human-in-the-loop correction with a
  narrow blast radius (one pair, one decision). This would be a proactive change to automatic
  detection quality — getting it wrong doesn't affect one pair, it shifts precision/recall for the
  whole corpus. Different mechanism, different risk profile, deserves independent design and
  validation.

## Known error-prone aspects

- **OCR quality varies** (EasyOCR confidence — see `ocr.confidence_min`/`ocr.lang_score_min`).
  Comparing noisy OCR text risks both directions: real duplicates with garbled OCR on one side look
  textually dissimilar (false negative), and two unrelated memes sharing a common caption template
  (e.g. "when you...") look textually similar despite being visually and semantically different
  (false positive).
- **Meme templates commonly reuse the same image with different text** — the exact "variant" case
  the `/review-duplicates` agent skill already special-cases. If OCR similarity were used as a
  *primary* signal rather than a filter layered on top of embeddings, it could actively make things
  worse: matching template reposts with different jokes as duplicates is backwards from the intent.
- **Multi-language corpus** (EN/ES/RU) complicates a naive text-similarity metric — would likely
  need per-language handling similar to `rules/normalize.py`'s existing lemmatization dispatch,
  rather than one global string-similarity threshold.

## Open questions for a future real design

- (a) filter/booster vs. (b) independent signal vs. both?
- Which similarity metric — check whether the existing `search.fuzzy_similarity_threshold` /
  phonetic-matching machinery (`rules/normalize.py`, `repository/ocr_lemmas.py`) already has
  reusable primitives before inventing a new one.
- Where this plugs into the pipeline: inside `rebuild_duplicates.py`'s candidate query (SQL-side,
  harder to integrate a text-similarity score cleanly) vs. a separate post-filter/annotation step
  (batch or on-demand) that adjusts or tags existing `tmp_duplicates` rows.
- Interaction with the dismissal-decisions feature: if OCR dissimilarity becomes a signal that
  *auto-suppresses* a candidate pair, does that write to the same `duplicate_decisions` table as an
  automatic decision, or stay entirely upstream (never let the candidate reach `tmp_duplicates` at
  all)? Writing to `duplicate_decisions` would conflate human-confirmed and algorithmic decisions in
  one table — the dismissal spec's audit-listing UI would then need a way to distinguish them (e.g.
  a `source` column). Flagging now so it isn't overlooked if these two specs are ever tackled
  back-to-back.

## Non-goals for this draft

This captures the idea and its risks for future design work — it does not propose an approach yet.
A follow-up brainstorming session should produce approaches and a recommendation before this
becomes a full design doc and progresses through this repo's usual
draft → approved → planned → implementation → done status lifecycle.
