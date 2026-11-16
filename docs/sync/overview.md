# Dev/prod data sync: overview

Each environment (`metal`, `general`, `it`) runs as two instances: a **dev** instance on the owner's workstation, where every data
pipeline runs (ingestion, OCR, embeddings, tagging, descriptions, deduplication), and a **prod** instance on a VPS with its own
Postgres, backend and disk. The `sync` package (`python -m sync.*`) moves data between them. This page explains what moves, in which
direction, who starts it and which safety rails exist. The commands are in [commands.md](commands.md), the day-to-day flow in
[first-seed-and-routine.md](first-seed-and-routine.md), failures in [troubleshooting.md](troubleshooting.md).

Source of truth for the design: `docs/superpowers/specs/2026-11-12-environment-data-sync/` (`README.md` has the decisions log,
`interfaces.md` the single contract of names, flags, files and exit codes; if this page and `interfaces.md` disagree, `interfaces.md`
wins). Status of the work: `tracker.md` in the same folder.

## What syncs: the three classes

Every table of `Storage/models.py` has exactly one class in `sync/registry.py`. The full list, with load mode and scope per table,
is the generated page [docs/sync-registry.md](../sync-registry.md) (never edit it by hand; `python -m sync.render --write`
regenerates it). Today: 46 tables, 22 class D, 6 class P, 18 class X.

| Class | Meaning | Origin | Direction | Tool |
|---|---|---|---|---|
| **D** | dev-origin data that prod serves: images, OCR, embeddings, tags, descriptions, profiles, notes, concepts, popularity absorptions | dev | dev to prod | `sync.promote` (rows), `sync.files` (image files) |
| **P** | prod-origin reader activity: impressions, saves, search history (and its tags), not-found reports, meme-from-text ratings; plus bug-report files | prod | prod to dev | `sync.pull` |
| **X** | dev-only scratch and admin data: batch runs, duplicate candidates and clusters, comics, recut cuts, trends, statistics snapshots, processing markers | dev | never synced | none |

Only `active`, unflagged images are promoted (`pending`, `rejected`, `cut_proposed` rows and files never leave dev). Image ids are
created on dev and kept on prod (no re-keying). Embeddings and other derived rows are carried as data; prod never runs a pipeline.
Disk assets (`excluded/`, `rejected/`, `pre-cuts/`, `converted_originals/`, the ingestion inbox, backups) never leave dev.

Because every table has one origin, data flows only away from its origin and nothing is merged. A dev-to-prod command can never
write a class P or X table; the pull can never write prod.

## Who initiates, and which way

- **Dev always initiates**, by hand, over SSH. Prod never connects to the workstation. There is no schedule; nothing syncs by itself.
- "Prod to dev" is a **pull** run on the workstation.
- The usual entry point is `python -m sync.run --env <env>`: handshake, pull, files, data, verify, in that order, with one combined
  report. Each step is also available alone.
- Files go before rows when adding (a row must never be visible without its file).
- The VPS side runs only timers (nightly backup, staging sweep, facts for the verify report); see [prod-operations.md](prod-operations.md).

## The decisions that shape daily use (plain words)

These are numbered as in the decisions log of the epic README.

- **26, dry run first, with a report.** Every sync command is a dry run unless you pass `--apply`. A dry run writes nothing on
  either side except a report folder, and prints the headline numbers plus a **plan hash** and the exact command to apply. The report
  lists what would be added, what would be overwritten (for images: which digest groups changed), what is unchanged, what is skipped
  and why, and what is left behind. You read it, then apply with `--apply --plan-hash <hash>`; the apply re-plans and refuses (exit 3) if
  the world changed since you read the report.
- **27, no removals for now.** Nothing is ever deleted from prod and no removal is built. An image you flag, cut, reject or delete on
  dev after it was promoted **stays visible on prod** (row and file) until removals are built. Such leftovers appear in the report as
  "left behind on prod (removals deferred)": counts and ids, informational, never an error. Replacing the derived rows of a
  changed image (a new tag, a cleared note) is an overwrite of that image and is in scope.
- **28, the pull plan hash is structural.** Readers change prod every minute, so the pull's hash covers only the shape of the plan
  (tables in scope, ratings mode, flags, which guards are tripped, whether bug reports are pulled), not row contents. The row-level
  detail is in the report. `sync.run`'s combined hash therefore contains a structural pull part and content-bound files and data parts.
- **29, typed confirmation names what is written.** `--apply` without `--yes` asks you to type `<env> <database>`. Commands that write
  prod (`sync.promote`, `sync.files promote`, `sync.run`) ask for `<env> <prod database>`; a stand-alone `sync.pull`, which overwrites
  dev's mirror of the class P tables, asks for `<env> <dev database>`. `sync.run --apply` asks once and passes `--yes` to its steps.
  `--yes` skips only that question, never a guard.

## Safety rails

1. **Identity handshake (H1 to H10)** before the first read of dev data or write of prod: right dev, right database name, right
   role (not superuser, not owner, no write on class P or X tables), the prod database marker `memes.environment`, the public
   capabilities answer, the gate's environment, not the same server as dev, id overlap, serving/seeding state, and the release check
   (alembic version equal and `memes.revision` equal to the dev working tree's commit). Details: [troubleshooting.md](troubleshooting.md).
2. **Per-environment everything**: database, roles, passwords, SSH keys, ports and compose project are separate for `metal`,
   `general` and `it`; a credential of one never works for another, and the tool compares every side with `--env`.
3. **Guards**, all evaluated before any write (exit 3 when tripped; the dry run exits 0 and shows them): zero promotable images,
   a dev with fewer than half of prod's images, mass change (more than 25 % of prod's images changed), model change, row-count and
   coverage regressions per table, empty or manifest-below-half file manifest, left-out images above 0.5 %, gate replace-per-day cap,
   pull shrink, growth and row ceilings. Each guard names the flag that lifts it, if any, and a few never lift (zero images, id
   overlap, any handshake step).
4. **Atomic units**: image rows move per batch in one prod transaction; files are published by `rename(2)` after a sha256 check, so a
   file appears whole or not at all; a killed run is repaired by running the same command again.
5. **Least privilege on prod**: the sync writer role can write only class D tables, the sync reader only reads class P, the backend
   only writes class P; DDL is an owner step done on the VPS. See [security.md](security.md).
6. **One lock per environment**: only one sync command at a time per environment (state directory lock); a second exits 2.
7. **Redaction**: reports, logs and exceptions carry ids, table names, counts and reason codes, never message text, rating text, row
   values, credentials or connection data.

## Where things live

| What | Where |
|---|---|
| Code | `sync/` (`registry.py`, `connect.py`, `config.py`, `report.py`, `promote/`, `files/`, `pull/`, `run/`, `verify/`, `roles.py`, `release.py`, `backup.py`, `render.py`) |
| State and reports (git-ignored) | `Storage/sync-state/<env>/` (`lock`, `pull_state.json`, `runs/<run id>/...`); see [commands.md](commands.md) |
| Tracked settings | `environments/settings.yaml` (defaults) and `environments/settings.<env>.yaml` under `sync:` |
| Secrets (git-ignored) | `environments/.env.<env>.sync` (only `SYNC_WRITER_PASSWORD` and `SYNC_READER_PASSWORD`) |
| Prod stacks, gate, roles SQL, backups | `deploy/prod/` ([README](../../deploy/prod/README.md)) |
| Generated registry page | [docs/sync-registry.md](../sync-registry.md) |
| Per-tool pages | [promote.md](promote.md), [files.md](files.md), [pull.md](pull.md) |

Dev vs prod stage of the application itself: the same backend code runs on prod with `APP_STAGE=prod`, which switches off every
route not marked `reader` in `Backend/app/stage_policy.py`; see [security.md](security.md) and `docs/sync/prod-operations.md`.
