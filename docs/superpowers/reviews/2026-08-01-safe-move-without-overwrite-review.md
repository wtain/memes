# Safe Move Without Overwrite — Review

Spec: `docs/superpowers/specs/2026-08-01-safe-move-without-overwrite-design.md`
Plan: `docs/superpowers/plans/2026-08-01-safe-move-without-overwrite.md`

Implemented via subagent-driven-development: 3 tasks, each with its own implementer + task
reviewer, followed by a final whole-branch review. No fix round needed — all findings from the
final review were Minor and parked rather than fixed.

## Per-task reviews

All three tasks passed their scoped review clean (spec compliant, no Critical/Important findings):

1. **`batch/utils/safe_move.py`** (`move_without_overwrite`, truncate-before-collision-check
   ordering, `MAX_FILENAME_LENGTH`/`_SUFFIX_RESERVE` constants) — approved, no findings.
2. **`move_flagged.py` wiring** — approved, no findings. Along the way, this implementer caught a
   real bug in the plan document itself: its "Run:" test commands literally said
   `cd H:\workspace_sandbox\memes` (the main checkout, not this branch's worktree), which would
   have silently tested stale, unmodified code. Fixed in the plan (commit `bed51f0`) before Task 3
   was dispatched.
3. **`ingest_hash_dedup.py` wiring** — approved, no findings. Verified against a real `ocrdb_test`
   database (213/213 full `tests/integration/` root).

## Final whole-branch review

**Ready to merge: Yes.** No Critical/Important findings. The review went further than confirming
no regression — it traced the one genuine cross-file risk directly rather than trusting the
spec's claim: `ingest_hash_dedup.py`'s own docstring says survivor files move into `BASE_PATH`
"same filename -- `extract_text_from_memes.py` later depends on that as its lookup key." The
reviewer read that lookup (`find_image_by_filename`, ending in `scalar_one_or_none()`) and found
that the **old** behavior — register with the original filename, then silently overwrite on
a collision — could leave two `Image` rows sharing a filename while only one file survived on
disk, which would raise `MultipleResultsFound` on the next OCR pass. This branch's move-then-
register-with-the-final-name keeps the DB filename and the on-disk name in lockstep, closing that
latent crash. Also independently verified (not just trusted) that nothing looks up files inside
`move_flagged`'s `excluded/` directory by filename after the fact, via a direct grep across
`Backend/` and `repository/`.

### Parked (non-blocking, no fix loop — all Minor per calibration)

- `ingest_hash_dedup.py`'s module-level docstring (flow summary) still says "same filename" and
  describes the old register-then-move order, contradicting the function's own updated docstring
  85 lines below — worth a follow-up doc fix.
- The reviewer disputed Task 3's own review note that `ingest_hash_dedup.py` "has no metrics
  infrastructure" — its `run()` does return a stats dict persisted via `update_stats`, so a
  `renamed` count could slot in with no new infrastructure, unlike `move_flagged`'s persisted
  `renamed_to_avoid_overwrite` counter. Reasonable to defer, but for the correct reason.
- `test_safe_move.py`'s top-level `MAX_FILENAME_LENGTH` import is unused (only the monkeypatched
  `module.MAX_FILENAME_LENGTH` is read) — inherited verbatim from the plan; `autoflake` would flag
  it.
- Truncation (not just an actual collision) can make `final_filename != filename` for a
  ~250-255-character name with nothing having collided, slightly mislabeling `move_flagged`'s
  `renamed_to_avoid_overwrite` counter. Vanishingly rare, harmless.

### Recommendation for a future spec (out of this plan's scope)

`Backend/app/services/image_store.py`'s `rejected/` directory is read back by filename (unlike
`excluded/`, confirmed write-only) via `undo_reject`, and shares the exact same-filename-collision
risk this branch fixed elsewhere — two rejected images sharing a filename would clobber each
other, and a later undo could restore the wrong image. Adopting `move_without_overwrite` there
needs a DB-filename-update companion change (unlike this branch's two callers, one of which never
reads the destination back by name and one of which persists the returned name) — the reviewer
flagged this as the utility's real genericity boundary, worth stating in its docstring whenever
that follow-up happens.

## Outcome

**Merged.** All three tasks implemented, individually reviewed clean, and the final whole-branch
review found no blocking issues — only Minor documentation/observability polish, explicitly parked
rather than looped on.
