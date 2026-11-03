# C1 comics data model: review

Spec: `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-02-c1-data-model-and-status-registry-design.md`
Plan: `docs/superpowers/plans/2026-11-03-c1-comics-data-model.md`
Range reviewed: `bb23224..8ab0eed` (tables and migration, name rules, repository lifecycle and panel operations, one fix round on
the lifecycle, one final fix wave).

Process: a per-task review after each of the four tasks, one fix round on the repository lifecycle, a final whole-branch review
on the most capable model, one fix wave and one scoped re-review. The migration was verified on a throwaway database
(upgrade from empty, downgrade, upgrade, one head).

## What was fixed

- Name uniqueness race: create and rename now translate a unique violation (inside a savepoint) to `name_taken`; the same for
  adding one image to two comics at once (`image_in_comics`).
- Search runs on the stored NFC, case-folded `name_key` with escaped wildcards, so Cyrillic case-insensitivity and composed vs
  decomposed accents behave the way name uniqueness does.
- `rename`, `delete` and `mark_built` take the comics row lock (a rename or delete racing a build could have changed a built
  comics); `get(lock=True)` is the locking read for C4.
- Moving an image into its own comics is deterministic (placed last).
- `mark_built` refuses a built image already used by another comics with a coded error.
- New `comics_panels.source_image_id` (plain id, no foreign key) so provenance can be linked to popularity edges after the image
  row is deleted.
- Names reject format and separator characters (zero-width, bidi override, line and paragraph separators) and the key is
  re-normalized after case folding.
- Documentation: ordinals in `docs/schema.md`, the recut rollout runbook counts and downgrade note, the derived-data and image
  status wording.

## Accepted out-of-brief edits

`tools/rollout_preflight.py` and `docs/batches/use-cases/screenshot-recut-rollout.md` (the runbook guard requires every new
revision to be listed), and `tests/integration/test_popularity_migration.py` (the model's source check now includes
`comics_build`). The reviewer found each minimal, correct and in line with how earlier revisions were handled.

## Not fixed, accepted (with reason)

- No test pins the three added row locks or `get(lock=True)`: pinning needs a two-session test. Tracked as D9 in the tracker, to
  be written with C4, which relies on the lock.
- The same-comics move test and the composed/decomposed key test would probably pass without their fixes (found by the re-review);
  the fixes themselves were verified by reading. Cheap to tighten later.
- `image_already_built` is a check-then-act; the unique index still protects the data.

## Deferred minors

An automated migration shape and downgrade-guard test; `source_image_id` missing from the `comics_panels` columns in
`tools/rollout_preflight.py`; dead variables in one race test; the module docstring says every mutator locks "as its first read"
(true before changing, not literally first); tests for the add_image re-raise branches. All listed in the tracker (D9) or here.
The discovery report counting `comics_build` as a re-drop is tracker D8, for C4.
