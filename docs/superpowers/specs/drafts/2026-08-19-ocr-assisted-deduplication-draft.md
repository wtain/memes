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

## Brainstorming session notes (2026-09-04)

Core principle raised for this round: **keep the duplicate/not-duplicate decision on a human**;
OCR should help a human split/mark clusters (e.g. a big cluster that's really one template with
many different captions), not drive automatic clustering. Also flagged: a prior automatic
deduplication pass produced false positives, removing visually-similar-but-different memes — this
is the concrete incident motivating "human stays in the loop."

**Relevant prior art found in-repo (not proposed here — already shipped):**

- `duplicate_decisions` table + `clusterize.py` anti-join filter
  (`2026-08-19-duplicate-dismissal-decisions-design.md`) — durable human "not duplicate" pair
  decisions, permanently excluded from future clustering.
- `tools/agent_duplicates.py` + `.claude/commands/review-duplicates.md` — already surfaces OCR
  text, embedding pairwise distances, and descriptions per cluster member; documented decision
  priority is explicitly **OCR text first, embedding distance second** (different text → template
  variant → keep both). Human/agent judgment call today, not an algorithmic filter.
- Ingestion Tier A/B review (`2026-07-24-ingestion-pipeline-design.md`) — Tier B exists specifically
  because loose embedding matches need OCR text to separate a real repost from a template variant,
  modeled on the same OCR-first priority. Always human-reviewed.

So "OCR as a human-assist signal, human decides" is already the established pattern in two places.
The open question isn't whether that's the right direction — it's what's actually missing in the
existing flows that this draft should fix.

**Open questions for discussion:**

1. Should this draft pivot away from options (a)/(b) above (both feed *automatic* detection in
   `rebuild_duplicates.py`/`clusterize.py`) toward pure tooling/UI work that never changes what's
   written to `tmp_duplicates`/`tmp_clusters`? Demote (a)/(b) to explicitly-rejected alternatives?
2. What's insufficient about `agent_duplicates.py`'s existing per-member OCR text + pairwise
   distances for a "huge templated cluster" today — does O(N²) pairwise text reasoning not scale
   past a few members? Or is the real gap the **web** `/duplicates` page, which (per the
   dismissal-decisions spec) shows only thumbnails + a "not duplicates" button, no OCR/distances?
3. Details of the "automatic dedup produced false positives" incident: was that `/review-duplicates`
   running semi-autonomously (agent calling `mark_flagged` on its own OCR-first judgment) and still
   getting variants wrong, or a different mechanism? Determines whether the fix is "better
   signal/UI for a human" vs. "the agent's judgment itself needs to be more conservative."
4. "Splitting huge clusters" — (i) presentation-only sub-grouping by OCR similarity during review
   (no DB write) vs. (ii) an actual change to `clusterize.py`'s `resolve_cluster` splitting math
   (OCR distance as a second axis)? (ii) reopens the same automatic-detection-quality risk this
   draft already flags.
5. If (i): what does a human do differently afterward — select-and-split a subset of members into
   their own group? Does a confirmed split persist as bulk-dismissed cross-group pairs (reusing
   `duplicate_decisions` as-is), or does grouping need a new, positive "same template" relationship
   distinct from a negative "not duplicate" one?
6. Case 2 in this draft (false negatives — real duplicates CLIP misses but OCR would catch) is a
   different problem — finding new candidate pairs, not organizing existing clusters. Keep it in
   this draft, or split into its own follow-up spec?
7. (Once scope is fixed) `pg_trgm` trigram similarity is already tuned for single-lemma search
   matching (`search.fuzzy_similarity_threshold = 0.35`, `fuzzy_min_lemma_length = 5`); whole
   multi-line, multi-language OCR blob comparison is a different regime and would want its own
   empirical check rather than assuming the same threshold transfers.
8. (Mechanics) OCR-empty images: "no signal, fall back to embeddings only" vs. "both empty →
   textually identical" (current `agent_duplicates.py` behavior)?
9. (Surface) CLI/agent tool only, the web `/duplicates` page, or both — they currently expose very
   different levels of OCR signal to the reviewer?
