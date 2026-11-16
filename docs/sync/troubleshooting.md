# Sync troubleshooting

Start with `python -m sync.doctor --env <env>` (handshake only, no report folder) and the report of the failed run:
`Storage/sync-state/<env>/runs/<run id>/report.md` (a run that fails the handshake or a precondition still writes one, with only the
handshake and precondition results). Messages and report entries carry codes, counts and ids only, never row values.

## Exit codes at a glance

| Code | What it means | First thing to do |
|---|---|---|
| 0 | clean (a dry run is 0 even if a guard would trip) | read the report |
| 1 | a step failed part-way, or a lock timeout on dev | read the message and the `result` block; run the same command again to resume |
| 2 | handshake or precondition failed; held lock; missing setting, secret file or tunnel | find the step or precondition below; nothing was written |
| 3 | a guard tripped before any write, `--plan-hash` differs, or the typed confirmation did not match (`sync.verify --fail-on warn`: warnings only) | read the guard in the report; re-run a dry run |
| 4 | verification found differences (`sync.verify` error check; promote's own verify phase) | read `verify.json` / the verify section; see "Verify failures" |

`sync.release` has its own table (0 ok, 1 step failed, 2 refused, 3 a grant differs) in [commands.md](commands.md).

## Handshake H1 to H10

Any failure is exit 2 with `handshake H<n> failed: <detail>`. The detail is a comma-separated list of codes.

| Step | What it checks | Detail codes and meaning |
|---|---|---|
| H1 | the dev side | `app_env_mismatch` (shell `APP_ENV` is not `--env`); `dev_url_kind_wrong` (read-only URL where a write-capable one is needed, or the reverse; `sync.pull` needs `DATABASE_URL`, the others `DATABASE_URL_READONLY`); `dev_db_mismatch` (`current_database()` differs from `sync.dev.database`, or that setting is still empty); `dev_has_memes_environment` (the dev database carries a prod marker: you are pointed at prod); `dev_ro_url_missing` / `dev_rw_url_missing` |
| H2 | tunnel and prod database name | `not_via_tunnel` (the connection is not `127.0.0.1:<sync.prod.local_port>`); `prod_db_mismatch` (`current_database()` differs from `sync.prod.database`). Tunnel start problems are exit 2 with the ssh failure (see "Tunnel") |
| H3 | the prod role | `prod_connection_kind_wrong`; `wrong_current_user` (the password file logs in as another role); `role_attr_*` (superuser, createrole, createdb, replication or bypassrls set); `member_of_table_owner`, `member_of_<x>_role` (the role is the owner, app, writer or reader in disguise); `missing_tables=N` (the schema lacks registry tables); `write_on_class_p_or_x=N`, `select_on_class_p_or_x=N`, `select_on_class_d_or_x=N`, `missing_write_on_class_d=N`, `missing_select_on_class_p=N`, `write_privilege=N` (grants differ from the registry: run `sync.release grants` on the VPS); `ratings_column_select_wrong` / `images_column_select_wrong` (column-level grants differ); `alembic_version_unreadable|writable`; `reader:...` the same for the reader role; `reader_connection_missing` |
| H4 | database marker `memes.environment` equals `--env` | `prod_environment_mismatch`: wrong database for this environment, or the marker was never set (`provision.sh apply`) |
| H5 | `GET <sync.prod.api_url>/api/diagnostics/capabilities` | `capabilities:<code>` (`api_url_invalid`, `api_url_not_https`, `unreachable`, `http_<status>`, `not_json`, `body_too_large`); `environment_mismatch`; `stage_not_prod` (the backend there does not run with `APP_STAGE=prod`). `sync.prod.api_url` is empty until the real hostname is known |
| H6 | the gate says the same environment | `gate:<code>` (ssh refused, key not installed, device mismatch, free space under the floor, unreadable probe); `gate_environment_mismatch`; `transport_missing`. Not run by `sync.promote`; shows `skipped:no_transport` in the doctor |
| H7 | not the same server as dev | `same_server`: the prod alias points at your own dev Postgres (tunnel pointed at localhost's real port, or an alias on the wrong host) |
| H8 | id overlap (never liftable) | `overlap_low=<found>/<prod images>`: fewer than half of prod's images exist on dev, so this prod is not this environment's (a metal prod against a general dev has overlap 0). An empty prod passes (`prod_empty`) |
| H9 | `memes.state` | `state_<x>_want_serving`: prod is `seeding` or unset, run `--first-seed`; `state_serving_want_seeding`: `--first-seed` on a serving prod is refused |
| H10 | release | `alembic_version_differs` (migrate prod, or check out the right commit); `revision_unset` / `revision_mismatch` (the working tree's `git rev-parse HEAD` differs from `memes.revision`: check out the released commit, or release the new one and set the marker); `dev_git_sha_unknown` |

## Preconditions of the data step (exit 2)

Listed in the report under `preconditions`, each with `ok` and a detail: `session_settings` (a digest session setting could not be pinned),
`schema_parity`, `column_parity` (a table's columns differ between dev and prod, or from `Storage.models`: a hand-edited database), `model_parity`
(more than one embedding model pair or taxonomy version among the promotable rows), `running_batches` (a dev batch job is running; wait or
`--ignore-running-batches`), `prod_lock` (another promote holds the prod advisory lock), `required_tables`, `files_report`.

`files_report` problems: no non-dry `files-promoted.json` found, wrong environment, older than `--max-report-age` (default 2 hours: run the files
apply again), or its manifest hash no longer equals the promotable rows (a `fix_image_formats` run, a flag or a status change happened after the
files apply: run files again). Without a valid files report the data step refuses to write.

## Guards (exit 3, before any write)

The report lists every guard with value, limit, tripped or not, and the lifting flag.

| Guard | Meaning | Lift |
|---|---|---|
| `zero_images`, `dev_emptied` | dev has no promotable images, or fewer than half of prod's | none: dev lost data or is the wrong dev |
| `mass_change` | more than 25 % of prod's images changed | `--allow-mass-change N` (N equals the planned count) |
| `model_change` | embedding model pair or taxonomy version differs from prod | `--allow-model-change` |
| `row_regression:<table>`, `coverage_regression:<table>` | dev has fewer rows or lower coverage than prod for the same images (a half-rebuilt dev) | `--allow-shrink TABLE`, after finding out why |
| `empty_manifest`, `manifest_below_half_of_prod`, `left_out_tripwire` | files: nothing to send, fewer than half of prod's regular files, or over 0.5 % of promotable images left out for name or file problems | fix dev data; no flag |
| `gate_replace_cap`, `prod_anomaly_blocking` | files: too many replacements today for the gate's cap; a non-regular entry on prod | edit `/etc/memes/gate.conf` on the VPS for the day; clean the anomaly on the VPS |
| `only_excludes_required_tables` | promote: `--only` leaves out a `sync.promote.required_tables` table (`embeddings`, `ocr_texts`) while the plan adds new images, so they would reach prod without it | name the required tables in `--only`, or drop `--only`; no lift flag |
| `dev_only_discarded:<table>` | pull: the mirror would delete dev-only rows of a class P table, more than `sync.pull.max_dev_discard_rows` (1000) or `sync.pull.max_dev_discard_ratio` (0.25) of the table's dev rows, or any on a first pull (no `pull_state.json`) | `--allow-dev-discard`, after reading the counts in the report (they are printed in the confirmation prompt too) |
| `empty_source` | pull: prod has no class P rows while dev does | `--allow-empty-source` |
| `shrink:<table>`, `growth:<table>`, `row_ceiling:<table>` | pull: a class P table shrank, grew past the factor, or passed the row ceiling since the last pull | `--allow-shrink TABLE`, `--allow-growth`; growth can be an abuse flood: look at prod first |

Exit 2 (usage): `--apply --yes` without `--plan-hash` on `sync.promote`, `sync.files promote`, `sync.pull` and `sync.run`: `--yes` skips only the typed
confirmation, so give the hash of the dry run (the printed apply command has it).

Also exit 3: `--plan-hash` differs (the world changed since the dry run), and a wrong typed confirmation.

## Lock contention

`sync: another sync command holds the lock` (exit 2): another `sync.*` command (or a forgotten terminal) of the same environment holds
`Storage/sync-state/<env>/lock`. Different environments do not block each other. If no process is running, the OS lock is gone with
the process; check for a hung `python -m sync...` or `ssh` and end it. The data step also takes a Postgres advisory lock on prod
(`prod_lock` precondition) so two workstations cannot promote at once.

## Resume after an interruption

Run the same command again.
- **Files:** batches already committed on prod stay; the next plan sends only what is still missing or changed. Leftover staging
  batches older than 6 hours are swept by a VPS timer and by the gate verb `sweep`. A tar uploaded as `.part` that never completed is
  harmless.
- **Data:** committed image batches stay (listed in `committed.json` in the run folder); an uncommitted batch was rolled back. A failed
  batch stops the run with exit 1 and a count of committed batches.
- **Data, `committed_unrecorded`:** exit 1 with "COMMITTED BUT NOT RECORDED" means the batch is on prod, or may be (the process was killed, or
  `committed.json` could not be written, after or during the prod COMMIT). Those images are listed under `committed_unrecorded` in the report, never under
  `failed`, and are not rolled back. Before every prod COMMIT the apply writes `inflight.json` (ids and recorded digests of the batch) into the run folder;
  it is removed once `committed.json` holds the batch. While it exists, `--verify-only` and the verify at the end of a run include that batch, so a
  difference shows up as exit 4 (an image that never reached prod is `image_missing`). Running the same command with the **same `--run-id`** adopts
  the batch into `committed.json` after reading prod (digests equal) or drops the record (not committed). A new run id leaves the old record in its own folder;
  `--verify-only` still finds it (it scans run folders newest first). The plain re-run is safe either way: the next plan sees committed images as unchanged.
  A resume with different `--only` tables than the first run is refused (`committed_json_tables_differ`).
- **Pull:** one dev transaction, so a failure leaves dev unchanged; a lock timeout on dev (`lock_timeout` 10 s, a dev writer holds a class P
  table) is exit 1 "retry". `pull_state.json` is written after the commit and again after the bug-report step.
- **`sync.run`:** stops at the first failing step, prints the completed and not-run steps; run again.
- **First seed:** the same command resumes it; there is no wipe mode.

## Tunnel and gate problems (exit 2)

The tunnel is opened and closed by the tool (`ssh -N -T -L 127.0.0.1:<sync.prod.local_port>:127.0.0.1:<sync.prod.pg_port> <sync.prod.ssh_alias>`
with `BatchMode`, `ExitOnForwardFailure`, `StrictHostKeyChecking=yes`). Typical causes: the local port is busy (16432, 16434 and 16436
are the only workstation ports sync binds; an earlier stuck `ssh.exe` holds it), the host key is not in `known_hosts` (connect once by hand with the
alias to accept it), the key is not loaded or the alias is missing in `~/.ssh/config`, or the VPS firewall. ssh's stderr goes to a temp
file, not the log. Do not open a tunnel by hand: the tool owns it (decision 18).

## Report categories, read carefully

- `added` and `overwritten` are what the apply would change. For images, `overwritten` lists the digest groups that differ; expect `core`
  after `fix_image_formats` or a popularity change, `clip` after an embeddings rebuild, `ocr`/`tags` after a rerun of those jobs.
- `unchanged` is a count only (ids appear in `report.json` for sections under 1000).
- `skipped_with_reason` per reason code: `missing_file` (the dev file is not in `BASE_PATH`), `name_problem` (a name the gate or the VPS
  filesystem cannot take), `hash_mismatch` (the file's sha256 differs from `images.content_hash`; run the dev repair), `missing_required_table`
  (a new image without `embeddings` or `ocr_texts`), `files_gate_miss` (the data step's image did not match the files report: files first),
  `deferred_changed_since_plan` (dev changed during the run: next run picks it up), `non_promotable_reference`, `waiting_for_files`
  (dry run only).
- **`left_behind`** (header "left behind on prod (removals deferred)") is not an error and never changes an exit code. It is what prod has
  and dev no longer promotes: images flagged, cut, rejected or deleted on dev after promotion, concept or absorption rows only prod has,
  files not in the manifest (including the old file of a renamed image). They stay on prod and visible to readers until removals are
  built (decision 27). If one must disappear now, that is a manual operation on the VPS by the owner, outside these tools.

## Verify failures

`sync.verify` exit 4 means a check has status `error`; `verify.json` lists each check with `id`, `status`, `severity`, a short `detail` and a `fix`.
- **Digest drift (D2 to D4)** `new` or `changed` images: run a promote; if you just ran one, dev changed afterwards or prod was edited (look at
  "prod drift" in the promote verify phase). While a dev batch runs, errors are downgraded to warnings (`unstable:dev_job_running`).
- **D6**: a class X table has rows on prod (approximate, from table statistics): something leaked scratch data; investigate on the VPS.
- **F1, F4**: file missing or different, or a non-regular entry, odd mode or owner in the images directory: look with the gate `list`.
- **S1 (grants)**: prod grants differ from the registry. The run `sync.release verify-grants` on the VPS prints `kind / role / object / privilege`;
  read by kind in [deploy/prod/README.md](../../deploy/prod/README.md), section "When verify-grants fails" (`missing`: run `grants`; `extra`: run `grants`
  then find who granted; `unclassified table`: add it to `sync/registry.py`, `python -m sync.render --write`, `python -m sync.roles --env <env>`, release
  again; `missing table`: the migration did not create it; `missing role`: `provision.sh apply`). Never grant by hand.
- **S5**: indexes missing or invalid after a first seed that has not been rebuilt yet (relaxed while `memes.state` is `seeding`).
- **S6 / A1 / A3 / S3 / S7 / S8 / A4**: backup age, growth alarm, disk, HTTP and containment checks. A check that cannot run is `skipped` with
  a reason code (`facts_unusable`, `gate_facts_missing`, `no_backup`, `account_probe_unavailable`), never an error.
- **Digest mismatch in the promote verify phase (exit 4)**: prod's digest of an image differs from the snapshot digest the apply promised;
  the ids are in the output. Re-run the promote (it overwrites the image again) and then `--verify-only`; if it recurs, someone is writing
  class D rows on prod outside the tools: check roles with `sync.doctor` and `sync.verify`.

## Other messages

- `tracked setting sync.<key> is not set for environment <env>`: fill the setting (`sync.dev.database`, `sync.prod.api_url`) in `environments/settings.<env>.yaml`.
- `environments/.env.<env>.sync is tracked by git; refusing to read it`: remove it from the index and keep it ignored.
- "ignoring ambient environment variables PROD_...": harmless; sync reads only the `.env.<env>.sync` file.
- `typed confirmation did not match; nothing was written` (exit 3): type exactly the printed string.
