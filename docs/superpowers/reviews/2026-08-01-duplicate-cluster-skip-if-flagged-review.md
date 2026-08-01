# Skip Already-Reviewed Duplicate Clusters — Review

Spec: `docs/superpowers/specs/2026-08-01-duplicate-cluster-skip-if-flagged-design.md`
Plan: `docs/superpowers/plans/2026-08-01-duplicate-cluster-skip-if-flagged.md`

Implemented via subagent-driven-development: 2 tasks, each with its own implementer + task
reviewer, followed by a final whole-branch review and one documentation-only fix round.

## Per-task reviews

Both tasks passed their scoped review clean (spec compliant, no Critical/Important findings):

1. **`ImageExtrasRepository.get_flags_bulk()`** — approved, no findings. `ImageExtrasRepository`
   was previously write-only (`set_flagged` only); this adds its first read method. Verified
   against a real `ocrdb_test` database.
2. **`cluster_already_handled()` + wiring into `detect_file_duplicates.py`** — approved, no
   findings. Along the way, a third instance (this session) of a subagent briefly operating
   against the main checkout instead of its worktree left a stray untracked copy of the new test
   file there — found, verified byte-identical to the properly committed version, and deleted;
   main confirmed clean afterward.

## Final whole-branch review

Found a real, substantive gap rather than rubber-stamping the spec's own framing: the spec's
leading motivating example — "an operator un-flags a cluster member because it wasn't actually a
duplicate" — is **not** actually protected against by the shipped `any(currently flagged=True)`
check, in the common 2-member cluster case. Un-flagging the previously-flagged member leaves both
members `False`, so the next run silently re-flags it, overriding the operator's correction.

The reviewer also traced why the obvious fix ("skip if a row exists at all") isn't free: this
script is itself the only other writer of `image_extras` rows, so that signal would make the skip
near-permanent for any cluster the script has ever auto-flagged, not just human-reviewed ones —
real added complexity, not a drop-in improvement.

**User decision:** accept and document the limitation rather than design a more sophisticated
signal now.

Separately, the reviewer independently verified (not just trusted) that `ImageExtras.flagged` is
a general-purpose "pending bulk operation" marker, not duplicate-specific — confirmed via the
field's own original design spec and the frontend's flag checkbox appearing on every meme card,
unrelated to duplication. Concluded this is still the right signal to use, because the flag is
self-clearing: `move_flagged.py`'s chained `unregister_deleted_images` call physically removes
flagged images and cascade-deletes their `image_extras` row, bounding any stale skip signal by the
operator's own `move_flagged` cadence rather than leaving it permanent.

## Fix round (1 of 1, clean)

- Added a "Known Limitation" section to the spec's Motivation, explicitly naming the un-flag
  scenario as unprotected and explaining why the "row exists" alternative was rejected.
- Added one sentence to the Design section explaining why a general-purpose flag is still the
  right signal here (transient, self-clearing via `move_flagged`'s cascade-delete lifecycle).
- Added the missing bullet to `Readme.md`'s `detect_file_duplicates` description.
- Documentation-only — no code changes, no tests to re-run.

**Process note:** the fix implementer subagent hit an external session/rate-limit error mid-run,
after it had already completed and committed the actual file edits — it only failed to finish
writing its own report file. The controller verified the commit's actual diff content directly
before dispatching the scoped re-review, since no report file existed to hand off. Re-review
confirmed all three items addressed with no new breakage beyond one cosmetic long-line-wrap nit,
which the controller fixed directly (pure reflow, no content change) rather than spinning up a
second fix round for something that small.

## Outcome

**Merged.** Both tasks implemented, individually reviewed clean; the final whole-branch review
surfaced a genuine spec/implementation gap and got an explicit user decision on how to handle it
rather than silently shipping a documented promise the code doesn't keep.
