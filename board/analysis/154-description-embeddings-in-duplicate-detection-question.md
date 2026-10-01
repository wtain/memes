# [P3] Question: should description embeddings take part in duplicate detection?

- **Priority:** P3
- **Area:** Analysis
- **Source:** audit notes; the user assumed they already did

## Problem

They do not: duplicates use CLIP, OCR-text embeddings and OCR lemmas only. Analyse whether description embeddings add anything for
pure-image memes (no OCR text), where the CLIP probe alone decides, weighed against the cost (the bge-large embedding must exist for
every candidate, Ollama descriptions are slow, and a corroboration rule like the OCR-lemma one would be needed). A possible
outcome is closing this as won't-do.

---

## Analysis (2026-10-01, session 12ec6cf0-9a05-40be-ad18-6802263c7ac5)

Recommendation: close as won't-do. Evidence below comes from read-only queries (role `DATABASE_URL_READONLY`, aggregates only) on all three
environments, run by the controller. Scripts: scratchpad `dup_desc_stats.py`, `q2.py`.

### Where CLIP decides alone

Text-heavy pairs already get OCR-text embeddings plus a lemma-overlap gate. Pairs where either side has OCR text but is not text-heavy
still use CLIP alone, but they have text to compare lexically; the task asks about *pure-image* memes (no OCR text at all).

### How big is the pure-image population

| | metal | general | it |
|---|---|---|---|
| active images | 18,088 | 32,328 | 1,422 |
| with no OCR rows | 454 (2.5%) | 875 (2.7%) | 5 (0.4%) |
| with a description embedding | 0 | 21,744 | 0 |
| no-OCR and described | 0 | 678 | 0 |
| CLIP candidate pairs in `tmp_duplicates` (active) | 188,276 | 805,795 | 12,204 |
| ...with both images having no OCR | 620 | 5,331 | 0 |
| ...of those, both described | 0 | 4,336 | 0 |
| human "not duplicates" decisions among CLIP pairs | 0 | 707 (1 in the no-OCR set) | 0 |

In `general`, the no-OCR pairs at a useful distance are very few: of the 5,331, only about 180 are within the 0.12 Tier B bound and about 10
within 0.06 (the rest are old rows kept from the earlier 0.3 threshold; `clusterize` only uses pairs at or below 0.05).

### Findings

1. **Tiny target.** Roughly 180 candidate pairs in one environment, about 10 near the clustering threshold. A new signal, its schema and
   review changes would serve almost nothing.
2. **The signal does not exist in two of three environments.** Description embeddings exist only in `general` (21,744 images). `metal` and
   `it` have none, so a rule depending on them would silently do nothing there (the same drift problem the coverage-gap diagnostics spec
   describes for the OCR chain).
3. **No evidence of a CLIP false-positive problem here.** Humans dismissed 707 pairs in `general`; only 1 involves two no-OCR images.
4. **Cost and risk.** Descriptions are LLM output, vary between prompts and runs, and rejected descriptions must be excluded (task 149), so
   it would need the same corroboration machinery as the OCR-lemma gate (calibration, thresholds, a coverage diagnostic) on top of the
   Ollama and bge-large dependency.
5. **Wrong fix for the bigger population.** The 27k not-text-heavy images with OCR text are where CLIP-only false positives were
   measured (spec 2026-09-18). If that class needs more help, reusing the existing OCR-lemma corroboration for those pairs is the cheaper
   direction. That is a separate question, not this task.

Revisit only if the pure-image population grows or a measured false-positive class appears among no-OCR pairs.

User decision 2026-10-01: keep open (not closed as won't-do). Direction to be decided; the analysis recommendation stands.
