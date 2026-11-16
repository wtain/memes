# Sync commands

Every command is `python -m sync.<tool>` run from the repo root on the **dev workstation** (use `.venv311`), except `sync.release`,
which runs inside the `memes-migrate` image on the VPS. All flags below were checked against `python -m sync.<tool> --help`.
Shared meaning of the flags is defined once in `docs/superpowers/specs/2026-11-12-environment-data-sync/interfaces.md` (section i);
overview and concepts are in [overview.md](overview.md).

## Rules common to every sync command

- `--env {metal,general,it}` is required and selects dev database, prod stack, ports and keys. There is no connection option and no
  `PROD_*` variable; ambient ones are ignored with a warning.
- **Dry run by default.** Without `--apply` nothing is written on either side except a report folder.
- **Report location and format.** Every run, dry or applied, writes a run folder
  `Storage/sync-state/<env>/runs/<run id>/` (run id `YYYYMMDDTHHMMSSZ-<8 hex>`, or the one `sync.run` passes down) holding:
  - `report.json`: the complete machine-readable report (counts, bytes and complete id lists per category; only the id lists of the
    large pulled tables are cut at `sync.report.max_ids`, 100000, with a count and range instead);
  - `report.md`: the same for humans, each list capped at the first N ids (`--top N`, default `sync.report.top_n` = 50, with a line
    "N more in report.json");
  - an empty file `dry` in a dry run's folder (no tool ever accepts input from a folder carrying it; the report is for you);
  - the same headline numbers are printed on stdout, with the plan hash and the exact apply command. `--json PATH` also writes
    `report.json` to PATH (`-` for stdout).
  Categories per tool and per table or file set: `added`, `overwritten` (images: with the changed digest groups `core, ocr, clip, desc, tags, profile, note`),
  `unchanged`, `skipped_with_reason` (reason codes such as `missing_file`, `name_problem`, `hash_mismatch`, `missing_required_table`,
  `files_gate_miss`, `deferred_changed_since_plan`, `non_promotable_reference`, `waiting_for_files`, `capped_by_limit`), and
  `left_behind` (informational, "left behind on prod (removals deferred)"). Also: `totals`, `guards` (each with value, limit, tripped
  or not, and the flag that lifts it), `estimate`, the handshake results H1 to H10, `preconditions`, the `plan_hash` and the
  `apply_command`. An apply adds a `result` block with what was actually committed. A run that fails the handshake or a precondition
  (exit 2) still writes a report with only those results. A report never holds message text, rating text, row values or credentials.
- **Plan hash and `--apply --plan-hash H`.** The dry run prints the plan hash. With `--apply --plan-hash H` the tool re-plans first and
  refuses with exit 3 if the hash differs (data changed since you read the report). Without `--plan-hash` the apply prints the
  fresh report and still asks for the typed confirmation. The `sync.pull` hash is structural only (decision 28); `sync.run`'s hash is
  the hash of its ordered section hashes.
- **Typed confirmation** (with `--apply`, unless `--yes`): `sync.promote` and `sync.files promote` ask you to type `<env> <prod database>`
  (promote reads the name back from the server, files uses `sync.prod.database`); `sync.run --apply` asks once, same text, and passes `--yes`
  to its steps; a stand-alone `sync.pull` asks for `<env> <dev database>` because it overwrites dev's mirror. A wrong answer exits 3 ("typed
  confirmation did not match; nothing was written"). `--yes` skips only this question.
- **Lock.** One sync command at a time per environment (`Storage/sync-state/<env>/lock`); a second one exits 2.
- **Banner**: `dev <env> (<dev db> @ localhost, read-only) -> prod <prod db> as <role> via ssh alias <alias>`.

## Exit codes (all tools)

| Code | Meaning |
|---|---|
| 0 | clean; a dry run is 0 **even when the report says a guard would trip** (the apply would then refuse with 3) |
| 1 | other failure; a partly applied run lists what is committed, re-run the same command to resume |
| 2 | handshake or precondition failed, including a held lock, missing settings or secret file, tunnel or connection problems |
| 3 | a guard tripped before any write, `--plan-hash` differs, or the typed confirmation was wrong. `sync.verify`: only warnings with `--fail-on warn` |
| 4 | verification found differences (`sync.verify`: any check with status `error`; a left-behind finding is never one; `sync.promote`'s own verify phase) |

`sync.release` has its own codes, below. `sync.roles --check`, `sync.render --check` and `sync.backup --check` use 0 and 1.

## `python -m sync.doctor --env E [--json]`

Runs the identity handshake only, with no early stop, and prints one line per step (or JSON). Writes no report folder. Takes the lock.
Exit 0 when every step passes, 2 otherwise. Note: the doctor currently passes no gate transport, so step H6 prints `skipped:no_transport`;
the gate is exercised by `sync.files`, `sync.pull` (bug reports), `sync.verify` and `sync.run`. Run it first after any setup change.

## `python -m sync.promote --env E [options]`

Promotes the class D rows of the promotable images to prod (additions and overwrites only). Details: [promote.md](promote.md).

| Flag | Meaning |
|---|---|
| `--apply` | perform the promote (otherwise a dry run) |
| `--yes` | with `--apply`: skip only the typed confirmation |
| `--plan-hash H` | with `--apply`: refuse (exit 3) if the re-planned hash differs |
| `--first-seed` | prod is in state `seeding` (`memes.state`); resumable by running again |
| `--verify-only` | only the verify phase (read only) against the newest `committed.json` of the environment; not combinable with `--first-seed` |
| `--only TABLE[,..]` | restrict to class D tables (parents are added) |
| `--limit-images N` | cap new and changed images per run; the global sets (concepts, absorptions) are then skipped |
| `--batch-size N` | images per prod transaction (default `sync.promote.batch_size`, 500) |
| `--max-report-age AGE` | age limit of `files-promoted.json` (`2h`, `90m`, `7200s`, `1d`; default 2h) |
| `--top N`, `--run-id ID`, `--json PATH` | shared report flags |
| `--allow-mass-change N` | lift the mass-change guard; N must equal the planned changed count exactly |
| `--allow-shrink TABLE` | lift the row-count and coverage regression guards of one class D table (repeatable) |
| `--allow-model-change` | lift the model-change guard (embedding model pair or taxonomy version differs from prod); not part of the plan hash |
| `--ignore-running-batches` | lift the "no dev batch is running" precondition |

Needs `files-promoted.json` of a files **apply** younger than `--max-report-age` and matching the manifest; without it nothing is written
(see [troubleshooting.md](troubleshooting.md)). Exit codes: 0, 1, 2, 3, 4 as above.

## `python -m sync.files promote --env E [options]`

Promotes the image files (additions and replacements only) through the VPS gate. Details: [files.md](files.md). `sync.files` has one
subcommand, `promote` (retire, restore and purge do not exist, removals are deferred).

| Flag | Meaning |
|---|---|
| `--apply` | send the files (otherwise a dry run) |
| `--deep` | re-read every dev file and let the gate re-hash its files before sending |
| `--batch-mb N` | tar batch target in MB, 150 to 500 (default `sync.files.batch_mb`, 300) |
| `--yes`, `--plan-hash H`, `--top N`, `--run-id ID`, `--json PATH` | shared flags |

Writes `files-promoted.json` into its apply run folder; `sync.promote` reads it.

## `python -m sync.pull --env E [options]`

Pulls the six class P tables (and bug-report files) from prod into dev's mirror. Details: [pull.md](pull.md).

| Flag | Meaning |
|---|---|
| `--apply`, `--yes`, `--plan-hash H` | shared; the confirmation here is `<env> <dev database>` |
| `--allow-empty-source` | lift the guard "prod has no rows in any class P table while dev has some" |
| `--allow-shrink TABLE` | lift the shrink guard of one class P table (repeatable) |
| `--allow-growth` | lift the growth and row-ceiling guards |
| `--skip-files` | do not pull bug reports |
| `--top N`, `--run-id ID`, `--json PATH` | shared flags |

Uses the write-capable dev `DATABASE_URL` (it writes the class P mirror on dev); the read-only URL is refused here.

## `python -m sync.run --env E [options]`

The driver: handshake, pull, files, data, verify under one run id, one combined report (one section per step), one confirmation.
Each step is an in-process call of the tool above, under one lock and one SSH tunnel. The apply stops at the first failing step with that
step's exit code and names the steps completed and not run; every step is resumable, so run the same command again. The later `data` section of a dry
run cannot see files not yet on prod and shows those images as `waiting_for_files` (counted as expected adds or overwrites).

| Flag | Meaning |
|---|---|
| `--apply`, `--yes`, `--plan-hash H`, `--top N`, `--json PATH` | shared; `--plan-hash` is the combined hash |
| `--first-seed` | prod is in state `seeding`; resumable by running again |
| `--stop-after STEP` | stop after `handshake`, `pull`, `files`, `data` or `verify` (for rehearsal) |
| `--run-id ID` | run id (default: a new one) |
| `--allow-mass-change N`, `--allow-model-change`, `--ignore-running-batches` | forwarded to the data step |
| `--allow-empty-source`, `--allow-growth`, `--skip-files` | forwarded to the pull (`--skip-files` skips bug reports only; the files step still promotes image files) |
| `--allow-shrink TABLE` | goes to the data step for a class D table, to the pull for a class P table (repeatable) |

The run folder also holds `run.json` (flags, steps with status and exit code, combined `plan_hash`). `--allow-*` flags do not
exist for the handshake, the id-overlap check, the zero-image guard or mixed models.

## `python -m sync.verify --env E [options]`

Read-only drift and health report of prod against dev (identity, schema, drift per table, files, grants, growth, freshness, containment).
Writes no data on either side; it writes `verify.json` (and a report) into its own run folder. Under `sync.run --apply` it is the last step.

| Flag | Meaning |
|---|---|
| `--json PATH` | also write `verify.json` to PATH (`-` for stdout) |
| `--deep-files` | F3: re-hash every file on prod (gate `hash`) and on dev (slow; minutes for a large corpus) |
| `--deep-net` | A4: also check the declared Content-Length refusal and the rate-limit burst against the public site |
| `--skip CHECK[,..]` | skip checks by id (listed as skipped with reason `skipped_by_flag`) |
| `--fail-on warn` | exit 3 when there are only warnings |
| `--top N`, `--run-id ID` | shared flags |

Exit codes: 0 all clear, 4 any check with status `error`, 3 only warnings with `--fail-on warn`, 2 could not run, 1 other. Checks that need a
real VPS (gate `disk`/`containment` facts, backup metadata, HTTP checks, the ssh account probe) have only run against mocks so far;
see [prod-operations.md](prod-operations.md).

## `python -m sync.roles --env E [--check | --stdout]`

Generates `deploy/prod/sql/roles.<env>.sql` (grants of the five prod roles) from the registry. No flag writes the file; `--check` exits 1
when the committed file differs from a regeneration; `--stdout` prints instead of writing. Dev-side, no database needed. A new table or
a class change requires regenerating and committing this file.

## `python -m sync.release [--env E] {all,grants,migrate,verify-grants}`

Release steps 2 and 2b on the VPS, run by the compose `migrate` job (`docker compose -p memes-<env> --env-file env/<env>.prod.env --profile ops run --rm migrate [step]`).
`--env` defaults to `APP_ENV`. Steps: `migrate` (`alembic upgrade head` as the owner role), `grants` (apply `roles.<env>.sql`), `verify-grants`
(compare the live privileges of the four service roles with the registry); `all` runs the three and stops at the first failure.
Exit codes: 0 ok, 1 a step failed, 2 refused (`APP_ENV` differs from the database marker, not connected as the owner role, a roles file that
differs from what the registry generates, no `DATABASE_URL`), 3 a grant differs from the registry. Never prints the URL. The full runbook is
[deploy/prod/README.md](../../deploy/prod/README.md).

## `python -m sync.backup [--write | --check]`

Prints, writes (`--write`) or checks (`--check`, exit 1 when stale) `deploy/prod/backup.tables`, the class P table list read by
`deploy/prod/backup.sh` and `restore.sh`. An unknown flag exits 2.

## `python -m sync.render [--write | --check]`

Regenerates [docs/sync-registry.md](../sync-registry.md) from `sync/registry.py` (`--write`), exits 1 when it is stale (`--check`), and prints the page
to stdout with no flag. Run `--write` after adding a table or changing a registry entry; `tests/sync/` fails while the page is stale.

## Settings that these commands read

Tracked, non-secret, under `sync:` in `environments/settings.yaml` and `environments/settings.<env>.yaml` (key table in
`interfaces.md` section b): `sync.state_dir`, `sync.ssh_bin`, `sync.sftp_bin`, `sync.dev.database`, `sync.prod.{database, writer_role, reader_role,
ssh_alias, gate_alias, upload_alias, local_port, pg_host, pg_port, api_url}`, `sync.tunnel.*`, `sync.files.batch_mb`,
`sync.promote.{required_tables, batch_size}`, `sync.pull.*` (including `ratings_mode`), `sync.report.*`, `sync.verify.*`. Two values are not yet
filled: `sync.dev.database` (empty by design until the owner names each dev database) and `sync.prod.api_url`; the tools fail loudly
while they are unset. The only secrets are `SYNC_WRITER_PASSWORD` and `SYNC_READER_PASSWORD` in the git-ignored
`environments/.env.<env>.sync`; the loader refuses a git-tracked file and warns when other accounts can read it.
