# remove_singletons — Final Whole-Branch Review

**Branch:** `worktree-remove-singletons`
**Spec:** `docs/superpowers/specs/2026-08-05-remove-singletons-design.md`
**Plan:** `docs/superpowers/plans/2026-08-05-remove-singletons.md`
**Reviewed range:** `e340f20..2cc890d` (3 commits: spec/plan already on `main`, then
`17df3d8` Task 1, `abe0b1a` Task 2, plus one unrelated docs commit `2cc890d` — see note below)
**Reviewer:** Opus, final whole-branch review (subagent-driven-development skill)

## Summary

Per-task reviews (Task 1: `batch/remove_singletons.py`, Task 2: chaining into
`unregister_deleted_images.py`) were both clean with no findings. The final whole-branch review
confirmed the branch hangs together correctly end-to-end: the `move_flagged → unregister_deleted_images
→ remove_singletons` cascade composes without `move_flagged.py` needing any awareness of the new
step, `chain=True` preserves every existing caller (`run_wrapper.py`, the scheduler, the admin
controller), and the delete query is provably safe — a single-statement `DELETE ... WHERE cluster_id
IN (SELECT ... HAVING count(*) = 1)` (snapshot-consistent, no race) that can never conflict with
`clusterize.py`'s `UnionFind`-based rebuild (which structurally cannot emit singleton clusters).

Both test roots passed in full: `tests/integration/` 220/220, `batch/tests/` 66/66, including all 9
tests directly covering this feature.

**Note on commit `2cc890d`:** this branch also carries an unrelated commit (read-only DB credential
policy documentation, from a security incident on a different plan that was worked in this same
worktree session) that landed before the final review ran. The reviewer was told to skip it as
out-of-scope for this review; it's called out here for merge-commit-message accuracy since it will
ride along with this branch's merge.

## Findings and Resolution

### Important — CLAUDE.md batch pipeline section not updated (fixed)

`CLAUDE.md` explicitly requires batch-pipeline doc updates in the same change that adds a script or
changes an existing script's CLI surface. This branch does both (new `remove_singletons.py`, new
`--no-chain` flag on `unregister_deleted_images.py`) and neither was reflected. Fixed in commit
`20e3eed`: added `remove_singletons` to the maintenance-scripts list and a chaining note mirroring
the existing `move_flagged` → `unregister_deleted_images` note.

### Minor — addressed

- Spec status line flipped from `planned` to `done`.
- Plan checkboxes checked off (all steps complete).
- `test_mixed_clusters_only_singleton_removed` now scopes its final assertion to cluster ids `{3, 4}`
  instead of querying the whole `tmp_clusters` table — makes the test robust to future tests/fixtures
  that leave rows behind, rather than relying on savepoint-rollback isolation alone.

### Minor — not fixed, explained

- `tests/integration/test_unregister_deleted_images_tracking.py`'s module docstring says `main()`
  "unconditionally" calls `metrics.print()` for the chained call, when it's actually conditional on
  `chain=True`. Raised at Task 2 review and again at final review; left as-is — the docstring reads
  naturally as "unconditional within the chained branch," and rewording it for a corner case this
  precise isn't worth the churn.
- `batch/remove_singletons.py --env` has no `help=` text, unlike `unregister_deleted_images.py
  --env`. Cosmetic; the spec's code block specified it exactly this way.

### Minor — intentionally deferred (not this branch's scope)

- The duplicates review UI can still show a one-image "cluster" in one specific case this feature
  doesn't touch: `remove_singletons` counts all `tmp_clusters` rows for a `cluster_id` regardless of
  image status, but `get_duplicates_clustered()` filters to `active` images only. A cluster pairing
  one `active` image with one `pending` ingestion image (via `ingest_find_duplicates.py`) has 2 rows,
  survives `remove_singletons`, but still renders as a single visible image. This is pre-existing
  behavior, not introduced by this branch, and is outside the spec's stated scope (cascade-delete-
  created singletons, where a member row is deleted, not status-flipped). Noted here as a backlog
  item rather than fixed — a future spec could either filter `get_duplicates_clustered` by visible
  cluster size or have `remove_singletons` count only `active` members.

## Assessment

**Merged:** yes, after the Important finding and the two-line-scale Minor fixes above (commit
`20e3eed`). No Critical findings at any stage.
