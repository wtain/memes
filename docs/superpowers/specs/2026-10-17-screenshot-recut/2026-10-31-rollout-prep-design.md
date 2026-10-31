# Rollout prep: runbook and preflight check - design

Status: done
Plan: docs/superpowers/plans/2026-10-31-rollout-prep.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (follow-up after R1 to R7, M1, M2a and M2b)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md (its "First rollout" paragraph)

The whole screenshot-recut epic is merged on `main` and none of it has run against a live database. The only rollout text is the
six-step prose list in `docs/batches/use-cases/screenshot-recut.md`. This project turns it into a runbook the owner can follow
step by step, one environment at a time, and adds a read-only preflight check that says whether an environment is ready for the
next step. It runs nothing live: every live step stays the owner's decision.

## What already exists

- The merged migrations, in chain order on `main`: `b8d4f1a6c935` (R1: `image_cuts`, `stale` columns), `c5e2a9d7b140` (M1:
  `process_events`), `d7a3b9e2c451` (R3a: `image_cuts.failure_reason`), `a4f7c1e92b68` and `c8e2f5a1d093` (popularity:
  `images.popularity`, `popularity_absorptions`, the `adjustment` source), `e9c4a7b3d812` (R6: `image_cuts` detected box),
  `d5b8e3a07c41` (popularity S2: `meme_saves`, head at the time of writing). The recut ones are not contiguous: an environment that
  is behind gets the popularity and saves migrations in the same `alembic upgrade head`.
- All seven upgrades are schema-only: they create tables, columns, constraints and indexes and rewrite no existing row
  (`images.popularity` and the `stale` columns use a server default). The downgrades drop what was added, except
  `c8e2f5a1d093`, which refuses to downgrade once an `adjustment` popularity row exists.
- Three always-on environments, each its own Postgres: metal 5432, general 5434, it 5436 (`environments/Environments.md`).
  Backups are `pg_dump -Fc` (`Storage/Readme.md`).
- A read-only database role `ocr_readonly` and `DATABASE_URL_READONLY` per environment (CLAUDE.md, "Live database access for
  agents"). Agents never get the main `DATABASE_URL`.
- `tests/integration/` builds its schema with `Base.metadata.create_all`, so the test database has no `alembic_version` table.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Two deliverables: a runbook and a read-only preflight script. Nothing in this project touches a live database | Every live step needs the owner's explicit go-ahead (R7 decision 7) |
| 2 | The preflight is a standalone module, `tools/rollout_preflight.py`, with its own short-lived engine on `DATABASE_URL_READONLY`. No `Storage.db` import, no admin endpoint | It must run before the app's schema is current, and a one-time need should not become a surface of the live app |
| 3 | The script refuses a role that can write (exit 2 before any other read) and sets `default_transaction_read_only=on` | The CLAUDE.md rule enforced in code, not only in a sentence |
| 4 | The head and the pending list come from the repo's alembic script directory; the database side is only `alembic_version` | No second source of truth for the chain, and a database revision the repo does not know is reported as a failure |
| 5 | Schema expectations are a small table `(table, columns, created by revision)`, checked against `information_schema`; a test ties every name in it to `Storage/models.py` | A pending upgrade is expected to be missing things, so "missing, created by <revision>" is not a failure; the list cannot drift unnoticed |
| 6 | The report is a plain checklist (`OK`, `WARN`, `FAIL`), never contains the URL or the password, and exits 0 ready, 1 not ready, 2 usage or connection error | Safe to paste into a message; usable in a script |
| 7 | The runbook's rollback is the dump taken at the start. `alembic downgrade` is allowed only right after the upgrade, before anything new was written | Downgrades drop the new tables, so writes made since the upgrade would be lost; the dump loses the same writes but is the one tested path |
| 8 | Environment order is `general`, then `metal`, then `it`; stop at the first failing step of an environment and do not start the next | Separate Postgres instances, and the recut is meant to be reviewed on `general` first |

## 1. The preflight script

`python -m tools.rollout_preflight --env {metal,general,it} [--base-path PATH] [--backup-dir PATH] [--min-free-gb 5]`

`DATABASE_URL_READONLY` is read from the shell only, never from an env file, and never printed. `--env` labels the report and gives
the `BASE_PATH` default, read as text from `environments/.env.<env>` (its database URL is never read). Each check is a function
returning `(status, detail)`, so each is tested alone.

| Check | Does |
|---|---|
| `role_is_read_only` | `current_user` and `has_table_privilege(current_user, 'images', 'INSERT,UPDATE,DELETE,TRUNCATE')`; a writable role stops the run with exit 2 |
| `revision_vs_head` | current `alembic_version` against the repo head; an unversioned database (no table or no row) is reported, not a crash; lists the pending revisions in order with their messages and marks the popularity and saves ones as part of the same upgrade; a revision unknown to the repo is a failure |
| `schema_matches_models` | each expected table and column is `present`, or `missing, created by <revision>` when that revision is pending, or a failure when the revision is applied but the object is missing |
| `image_status_counts` | `images` by status; `cut_proposed > 0` before the first recut run is a warning |
| `filesystem` | `BASE_PATH` exists, is a directory and accepts a probe file (created and removed; the only write anywhere), `BASE_PATH/pre-cuts` is creatable, free space of at least `--min-free-gb` on `BASE_PATH` and on the backup directory |
| `active_jobs` | any `batch_runs` row with status `started` is a failure and names its kind |

## 2. The runbook

New `docs/batches/use-cases/screenshot-recut-rollout.md`. The six-step "First rollout" section of `screenshot-recut.md` becomes a
link to it, and the R7 spec's paragraph points at it.

0. **Before anything.** Run `tests/integration/` on `main` against the test database and read the result. Find out which code each
   always-on backend runs. They start with `--reload`, so if one runs from the main checkout, merged code is already serving
   against an unmigrated database; the preflight's revision line shows the gap. This is an open question for the owner (below).
1. **Per environment, in order, stop at the first failure:**
   1. Preflight before. `NOT READY` only because migrations are pending is expected; the pending list must match the seven
      revisions above.
   2. Backup with `pg_dump -Fc` into `Storage/backups/` (dated, as in `Storage/Readme.md`), then `pg_restore --list` on the file
      and record its size.
   3. Quiet the writers: no batch job running (the preflight checks it), nobody on `/duplicates`, `/ingestion` or the recut page.
   4. `alembic upgrade head` from `Storage/` with that environment's `.env` loaded.
   5. Preflight after; expect `READY` and the head revision.
   6. Restart that environment's backend on matching code (rebuild the image first where one is used: it now needs Pillow,
      `batch/utils/{__init__,recut_cut,file_hash}.py` and write access to `BASE_PATH`). Smoke test: `/api/diagnostics/health`,
      `/api/images?limit=1`, `GET /api/admin/batches/metrics?process=recut`, and `/admin/batches` showing all four metric panels.
2. **First recut pass, `general` only:** `detect_recut_crops --limit 500`, `recut_pipeline`, then review a few dozen cuts. Two things
   have never run for real and are watched: the `refresh_recut_cuts` GPU steps, and the box-adjust UI in a real browser (tall and
   wide images, touch).
3. **What to record afterwards:** the read-only queries for the first live numbers: approve rate by confidence band (the R7
   table), and the first `ingest_tier_*` and `library_duplicates` panels once those pages have been used.

Each step carries an "if this fails" line:

- Preflight or backup fails: nothing changed; fix and re-run.
- `alembic upgrade` fails midway: `env.py` runs the whole upgrade in one transaction, so a failure rolls back all of it; read the error and
  re-run the preflight to confirm the starting revision. Never edit `alembic_version` by hand.
- Smoke test fails with no new writes yet: `alembic downgrade` to the revision noted before the upgrade; if that fails, restore the dump.
- Anything fails after the app wrote new data: restore the dump of that environment only; the cost is the writes since the backup.
- A `recut_pipeline` step fails: trigger it again (each step selects only what is still waiting).

## 3. Open question for the owner

Whether the always-on backends run from the main checkout with `--reload` (then merged code already serves an unmigrated database
and step 0 is urgent) or from a separate copy or image. The repo cannot answer it. The runbook asks for the answer in step 0; the
design does not depend on it.

## 4. Docs touched

`docs/batches/use-cases/screenshot-recut.md` (link instead of the six steps), the R7 spec's rollout paragraph (link),
`docs/batches/inventory.md` and `CLAUDE.md` (one line on the preflight tool, which is shell only and not a batch job), and this
epic's tracker (a row for this project).

## 5. Testing

`tests/integration/test_rollout_preflight.py`, on the test database inside a rolled-back transaction:

- `role_is_read_only`: false for the `ocr` test role; true for a role the test creates inside the transaction with `SELECT` only;
  the CLI gate exits 2 on a writable role.
- `revision_vs_head`: unversioned database; at the head; at an older revision (pending list in order, popularity and saves marked);
  a revision unknown to the repo fails. The test creates `alembic_version` inside the transaction.
- `schema_matches_models`: present, missing with its revision pending, missing with its revision applied (failure); a test that every
  name in the expectations table exists in `Storage/models.py`.
- `image_status_counts`: counts, and the `cut_proposed` warning.
- `filesystem`: a temp directory passes; a missing path, a file instead of a directory and a threshold above the free space fail.
- `active_jobs`: a `started` `BatchRun` fails and names its kind; none passes.
- The report never contains the password of a URL passed through the formatter.
- `tests/docs/`: the runbook is linked from `screenshot-recut.md`, and the revisions it lists as pending match the chain (a test
  reads the alembic script directory), so the runbook cannot go stale when a migration is added.

## 6. Out of scope

Running anything against a live database, including the preflight itself (the owner runs it with the read-only URL); a dry run on a
restored copy of a backup (a possible later project); the deferred minors of R2 to R7, M2a and M2b (not recorded in the repo, so
they need a fresh review pass of their own); any change to the recut code; scheduling decisions.

## 7. Acceptance

- `tools/rollout_preflight.py` exists, is read-only by construction, and its tests pass.
- The runbook has exact commands, the order across environments, a failure line per step, and a rollback that matches what the
  migrations actually do.
- `screenshot-recut.md` links to the runbook and no longer carries a second copy of the steps.
- The doc guard fails when the runbook's pending-revision list differs from the alembic chain.
- Nothing ran live.
