# Search Canonization — Final Whole-Branch Review

**Branch:** `worktree-search-canonization`
**Spec:** `docs/superpowers/specs/2026-08-08-search-canonization-design.md`
**Plan:** `docs/superpowers/plans/2026-08-08-search-canonization.md`
**Reviewers:** Sonnet (task review) then Opus (final whole-branch review), subagent-driven-development skill

## Summary

Originally three narrow, fixed-rule text canonizations added to `rules/normalize.py` — Cyrillic
ё→е, British/American spelling variants, and negative-contraction expansion. The task-level
review approved all three cleanly (data tables verbatim, correct placement, zero scope creep,
14/14 new tests passing). The final whole-branch review found the ё→е piece was both unnecessary
and actively harmful, backed by direct verification against real code and real environment data —
it was removed after user consultation. The branch now ships two canonizations: spelling variants
and contraction expansion.

## Findings and Resolution

### Task-level review (Sonnet): Approved, no Critical/Important findings

Independently re-ran all 14 new tests and the module-import/count check, confirmed both lookup
tables were byte-for-byte identical to the plan's specified contents, confirmed the exact required
placement of both new checks (`SPELLING_VARIANTS` lookup as the literal first statement in
`lemmatize_word()`; `CONTRACTION_EXPANSIONS` handling before `normalize()`'s length/digit filters),
and confirmed zero scope creep. One cosmetic Minor (a test class landed near-but-not-immediately-
adjacent to its plan-specified location) was left unfixed as not worth a commit.

### Final whole-branch review (Opus): Critical finding on ё→е, otherwise clean

**Critical — ё→е was unnecessary and actively harmful; removed.** The reviewer traced the actual
behavior against real code and real data rather than reasoning abstractly:

- **The premise was false.** pymorphy3 is ё-*restoring*: `morph.parse("все")[0].normal_form` is
  already `"всё"` for known words. Verified directly, both before and after the change, with
  identical output — the tokenize-time ё→е fold did nothing for "все"/"всё", the exact case it
  was built to fix.
- **Where it did change behavior, it broke real production data.** Concept-vocabulary loading
  (`lemmatize_word_autodetect()`) never calls `tokenize()`, so it and OCR-text matching
  (`normalize()`, which does) would have stopped agreeing on ё-containing forms — traced against
  the real `general` environment's `concepts.general.yaml` and found 15 concept-vocabulary entries
  that would silently stop firing (e.g. vocabulary `"тиндёр"` vs. normalized text `"тиндер"`).
  Separately, 8 existing `ImageTag.value` rows containing ё (stored verbatim, never normalized)
  would have become unsearchable. Neither is fixable by re-running `build_ocr_lemmas.py` — both
  live in YAML vocabulary and stored tag values, outside what a lemma-index rebuild touches.
- **Root cause:** the change canonicalized toward `е` at input time, while pymorphy3 (the
  pipeline's dominant lemma authority) already canonicalizes toward `ё` at output time — two
  opposing conventions in one pipeline.

Presented to the user with both options (drop entirely, or the more invasive fix of canonicalizing
toward pymorphy3's own convention at lemma-output time plus rewriting the affected YAML tag
values). User chose to drop it entirely, since it wasn't closing a real gap in the first place.
Removed: the two ё/Ё entries from `_CHAR_NORMALIZE`, and the two test classes
(`TestTokenizeCyrillicYoNormalization`, `TestNormalizeCyrillicYoEquivalence`). Re-ran the full
`tests/rules/` root after removal: 144/144 passing (147 minus the 3 removed tests). Independently
spot-verified the reviewer's core claim directly against the real analyzer post-removal: "все" and
"всё" still produce identical, overlapping lemma sets, confirming pymorphy3 genuinely already
handles this case with no code from this branch.

**Important — fixed.** The spec's Out-of-scope section claimed the canonizations "take effect the
next time [`build_ocr_lemmas.py`] runs (full or incremental — a canonization applies at
lemma-computation time regardless of mode)". This was wrong independent of the ё→е question:
`--incremental` mode only processes images with no `ocr_lemmas` rows yet, so already-indexed
images' lemmas are never recomputed — the Rollout section's own "full mode required" requirement
(already correct) directly contradicted this claim. Removed the incorrect sentence from
Out-of-scope; Rollout's full-rebuild requirement stands as the single source of truth.

**Minor — confirmed no action needed:** `rules/phonetic.py`'s own internal ё-handling rules
(`_CONSONANT_VOWEL_MAP`, `_J_MAP`, `_VOWEL_J_MAP`) are not dead code — since pymorphy3 restores ё
in `normal_form`, lemmas reaching `russian_metaphone()` still contain ё regardless of this branch.
Moot now that ё→е was dropped, but recorded so it isn't re-investigated.

**Minor — confirmed no collision risk in the two shipped features:** the reviewer checked all 54
`SPELLING_VARIANTS` keys and all 15 `CONTRACTION_EXPANSIONS` keys against the real
`concepts.general.yaml`/`concepts.metal.yaml` vocabulary files for collisions — none found. Also
checked the `SPELLING_VARIANTS` mechanism against the Spanish-collision hazard that motivates
`_stem_lemma_ids` being query-time-fallback-only in `repository/ocr_lemmas.py` (concern: could an
exact whole-word lookup accidentally fire on an unrelated Spanish word) — concluded the hazard
class doesn't apply here, since this is a curated exact-match lookup, not a suffix transformation
that could fire on unlisted words.

## Assessment

**Merged:** yes, after removing ё→е (this commit) plus lifecycle bookkeeping. No Critical findings
remain. The branch ships exactly two of the originally-designed three canonizations —
British/American spelling variants and negative-contraction expansion — both independently
verified against real production vocabulary data with zero collision risk found.
