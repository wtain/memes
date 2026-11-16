# `sync.pull`: pull reader activity, prod to dev

Design: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p4-pull-reader-activity-design.md`. Flags and exit codes: [commands.md](commands.md). Classes: [docs/sync-registry.md](../sync-registry.md).

Copies what readers did on prod (the six class P tables: `meme_impressions`, `meme_saves`, `meme_text_ratings`, `search_history`, `search_history_tags`, `search_not_found`) into dev's mirror of them,
and pulls bug-report files. Prod is only read (as the `sync_r` role); the only thing ever deleted on prod is a bug-report copy that was received and verified. Dev-made activity is disposable
and may be replaced by the mirror.

## Flow

1. Handshake H1 to H5 (H6 when bug reports are pulled); the dev side must be the write-capable `DATABASE_URL` (the read-only URL is refused) and its database must not carry a `memes.environment` marker.
   The alembic comparison (H10) is not run: the tool compares the columns of the six tables and `images.id` instead. A differing `alembic_version` alone is a warning, since dev is usually one migration
   ahead during a release.
2. One prod `REPEATABLE READ READ ONLY` snapshot for all six tables, the image id list and the ratings fingerprint.
3. Plan and report per table: `added`, `overwritten` (including dev-only rows the mirror discards), `unchanged`, `skipped_with_reason`, `left_behind` (rows kept on dev by the ratings rules). Plan hash is structural.
4. Apply (after typing `<env> <dev database>`): one dev transaction takes `SHARE ROW EXCLUSIVE` locks on the written tables (a 10 s lock timeout; exit 1 "retry" if a dev writer blocks it), deletes children first and
   copies in binary from prod's snapshot (text columns cut at `sync.pull.text_cap`, default 2000 characters). Large FK-less tables are truncated instead of deleted above `sync.pull.truncate_threshold`.
   `meme_impressions` gets its sequence moved past `max(id)`. A failure leaves dev unchanged.
5. `pull_state.json` is written right after the dev commit and updated after the bug-report step (snapshot time, counts, ratings fingerprint, bug-report outcome).
6. Bug reports: files older than 60 seconds, with allowed extensions and size caps (`sync.pull.bug_reports.*`), are fetched through the gate (`pull-list`, `pull-tar`), checked against their sha256, written to dev, and
   only then deleted on prod (`delete-verified`). `--skip-files` skips this step.

## Ratings

`sync.pull.ratings_mode` is `skip` (default, while prod's ratings routes are off) or `mirror` (set together with turning those routes on). In `skip` the dev ratings table is untouched. In `mirror` a local ledger of
received keys (`ratings_ledger.json`, hashes only) decides what may be deleted on dev (only keys that were received from prod and are gone there); dev-made ratings are never deleted; rows for images missing on dev are skipped; any
rating deleted or overwritten on dev is first appended to `ratings_archive.jsonl`, the only file holding rating text (restore from it is manual).

## Guards (exit 3)

`empty_source` (prod has no class P rows but dev does; `--allow-empty-source`), `shrink:<table>` (a count below the previous pull's, or a drop over 50 % in the two tables that can lose rows by
"forget"; `--allow-shrink TABLE`), `growth:<table>` and `row_ceiling:<table>` (growth beyond `sync.pull.max_growth_factor` and `min_growth_rows`, or over `sync.pull.max_rows_per_pull`; `--allow-growth`).
A lost `pull_state.json` makes the next pull a first pull.

## Consumers of pulled text must sanitise (rule)

Everything pulled from prod is untrusted text typed by anonymous readers: search text, facets, not-found text, rating text, bug-report files. Control characters are kept as they are so that the mirror equals prod's rows.
**Any code that prints, renders, exports or opens this text must strip control characters and neutralise spreadsheet formulas** (leading `=`, `+`, `-`, `@`, tab, carriage return), must not execute it and must not
interpolate it into SQL or a shell. `tools/discovery_report` already sanitises its output (P5a-T8); the evaluation export (`evaluation/ratings_job.py`, `python -m evaluation export-ratings`) is a consumer of rating text and
has not been reviewed against this rule. See [security.md](security.md).
