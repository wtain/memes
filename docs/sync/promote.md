# `sync.promote`: promote data, dev to prod

Design: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p2-promote-data-design.md`. Flags and exit codes: [commands.md](commands.md). Tables: [docs/sync-registry.md](../sync-registry.md).

Writes the class D rows of the promotable images (status `active`, not flagged) to the prod database of the same environment, **additions and overwrites only**. It never writes a class P or
X table, never deletes an image row, and never deletes a global-set member. The files go first ([files.md](files.md)); this command refuses to write a row whose file is not on prod.

## Flow

1. **Handshake** H1 to H5 and H7 to H10 ([troubleshooting.md](troubleshooting.md)), then preconditions: schema, column and model parity, no running dev batch, the prod advisory lock, the
   completeness floor settings, a valid `files-promoted.json`.
2. **Plan.** Per image and digest group (`core, ocr, clip, tags, desc, profile, note`) a digest is computed on both sides in sessions pinned to UTC, ISO dates and explicit alphabetical
   column lists (columns of rolling bookkeeping such as rebuild timestamps and surrogate ids are excluded by the registry's `digest_exclude`). Sets: `new` (reported `added`), `changed`
   (reported `overwritten`, with the changed groups), `unchanged`, and `left_behind` (on prod, not promotable on dev, informational). A new image without an `embeddings` and an `ocr_texts`
   row is held back (`sync.promote.required_tables`).
3. **Guards** (exit 3 before any write): dev emptied or zero images (no lift); mass change over 25 % (`--allow-mass-change N`); model change (`--allow-model-change`); row-count and coverage regression per table
   (`--allow-shrink TABLE`). Not applied: mass change in a first seed.
4. **Report** in `Storage/sync-state/<env>/runs/<run id>/`; the dry run stops here.
5. **Apply:** after the typed confirmation (`<env> <prod database>`), per batch of `sync.promote.batch_size` images (default 500): one dev `REPEATABLE READ READ ONLY` snapshot read, one prod
   transaction (changed images: children deleted first, then re-inserted; `images` upserted; the rest by binary `COPY`); the batch's digests are appended atomically to `committed.json`. Then the global sets in separate
   transactions: concepts upserted by id (nothing deleted, `setval` afterwards); popularity absorptions by insert-or-close per loser, in the order that keeps its shape checks valid. Last `setval` and `ANALYZE`.
6. **Verify phase:** digests of the committed images are recomputed on both sides and compared with what the apply promised (exit 4 on a difference); `left_behind`, images dev changed since, and "prod drift" are informational.
   `--verify-only` re-runs just this phase.

## Prod grants of the writer

The prod writer role holds `SELECT, INSERT, UPDATE` on every class D table and `DELETE` only on the tables this command really deletes from: the derived tables
of an image that is replaced (registry `TableSync.delete_ok`: class D, mode `replace_per_image`). It has no `DELETE` on `images`, on the upsert tables or on the global sets
(`concepts*`, `popularity_absorptions`); the grant list in `deploy/prod/sql/roles.<env>.sql`, `sync.release verify-grants` and the role tests all derive from that one rule.

## Failure and resume

A batch is all-or-nothing. After an interruption run the same command; committed batches stay and the next plan continues from the digests. No prod write happens outside a transaction that
contains the image's `images` row, except `setval`, `ANALYZE` and the per-loser absorption transactions.
The one window is between the prod COMMIT and the append to `committed.json`: before the COMMIT the batch's ids and digests are written to `inflight.json`; if the
process dies or the append fails, the report names those images as `committed_unrecorded` (never as rolled back), and `--verify-only` and the verify phase check them
([troubleshooting.md](troubleshooting.md)). Database errors are reduced to SQLSTATE, constraint and table (never row content).

## Things to know

- A **changed** image is overwritten whole: a removed tag, a cleared note or a removed remark leaves prod with it. Popularity, every stale flag and description feedback are part of the digest.
- An image **flagged, cut, rejected or deleted on dev after promotion stays on prod** (row and file), reported as left behind. A renamed image leaves its old file behind. Removals are deferred (decision 27).
- `--limit-images N` skips the global sets. `--only TABLE` adds parents; while new images are planned it must include the required tables, otherwise the guard
  `only_excludes_required_tables` refuses (exit 3, no lift).
- `--apply --yes` needs `--plan-hash` (usage error otherwise). `--batch-size` and `--max-report-age` are not part of the plan hash: the apply command printed by the dry run
  carries them, and changing them before the apply only changes the transaction size and the allowed age of the files report.
- No flag lifts: zero images, id overlap, any handshake step, mixed embedding models among the promoted rows.
- The first seed (`--first-seed`) is the same path, requires `memes.state = seeding`, and never flips the state; see [first-seed-and-routine.md](first-seed-and-routine.md). The report of a first seed names the tables whose HNSW and GIN
  indexes are missing as informational preconditions `rebuild_indexes:<table>` (detail `missing=<n>`), or a single `rebuild_indexes` entry `all_present`, and the console prints the owner steps.
  The promote never changes `memes.state`.
