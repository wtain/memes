# P6 and P7: verification, drift report, runbooks and operations (design)

Status: implementation (built on branch worktree-env-data-sync, unmerged, no real VPS, see tracker.md "Built state" 2026-11-16; text of the design: revised 2026-11-12; reconciled with `interfaces.md` 2026-11-12; revised again for decisions 26 (dry-run report) and 27 (no removals for now) on 2026-11-12)
Interfaces: state files (`verify.json`, `run.json`, `committed.json`, `report.json`, ...; the report format is `interfaces.md` k), settings, handshake steps, gate verbs, flags and exit codes are defined in `interfaces.md`; this document owns the checks, the driver behaviour and the runbooks.
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (built), P2 (digests, verify phase, `committed.json`, shared modules SC and the report module), P3 (file manifest, gate, report), P4 (pull state), P5 (capabilities, roles, per-environment stacks), decisions log in `README.md` (1 to 27; 17 to 27 override older text).
Two projects in one document because they share the rehearsal: **P6** is a read-only command that says whether prod equals dev and is healthy and safe; **P7** is the runbooks, the driver and the docs that make the whole epic operable, and the acceptance run on a throw-away VPS.

# Part A: P6, verification and drift report

## Intent

An independent, read-only answer to "is prod what dev says it should be, and is it healthy and safe?", runnable at any time, after every promote, and before a release. P2's verify phase checks only what P2 just wrote; P6 checks everything an operator would
otherwise have to remember: identity, data, files, schema, privileges, containment, abuse limits and growth, health, freshness. One run covers **one environment** (decision 17); the report of three environments is three runs.

## Goals / non-goals / anti-goals

- Goals: one command and one report (human and JSON, in the report vocabulary of `interfaces.md` k: what a promote would add or overwrite, what is left behind on prod) with severity levels; checks for identity, data, files, schema, grants, runtime, growth and sync freshness; exit codes usable from a script; no write on either side; no row content in the output.
- Non-goals: repairing anything (it names the command that repairs it); monitoring and paging (a growth alarm in a report the owner reads is enough, decision 23); checking dev's internal consistency (tests and `tools/rollout_preflight` do that).
- Anti-goals: a check that needs a writable connection; a report that prints search text, message text, credentials, hostnames beyond what the operator typed, or file names beyond the anomaly paths needed to act; a green report that skipped a check silently (every skipped check is listed with its reason).

## Command

`python -m sync.verify --env {metal,general,it} [--json PATH] [--deep-files] [--deep-net] [--skip CHECK,..] [--fail-on warn] [--top N] [--run-id ID]`

Connections (decisions 17, 18, 21): no `PROD_*` variables and no hand tunnel. The tool reads `environments/.env.<env>.sync`, opens the tunnel itself through the shared modules built by P2 (work item SC: `sync/config.py`, `sync/tunnel.py`, `sync/connect.py`), and uses this environment's roles (`memes_<env>_sync_r`, `_sync_w`; `interfaces.md` d):
dev through `DATABASE_URL_READONLY`; prod class P and catalog through `sync_reader`; the class D digests through `sync_writer` **opened read-only** (`default_transaction_read_only`), because `sync_reader` has no `SELECT` on D tables by design. Both sides are read inside one
`REPEATABLE READ, READ ONLY` transaction (P2 review H5), so digests are not torn by a running dev job. The public API address is the tracked setting `sync.prod.api_url`; the ssh aliases are `sync.prod.ssh_alias` (tunnel) and `sync.prod.gate_alias` (gate). A missing `api_url` or `gate_alias` makes S0 exit 2; a missing optional input (for example no backup yet) skips only the dependent check with a reason.

## Checks

**S0 runs first and gates everything else: identity handshake** (`interfaces.md` e, steps H1 to H10: database name, role, database-level `memes.environment`, capabilities `environment` and `stage`, gate `handshake` environment, all equal to `--env`; dev is not prod). A failure is exit 2 and no other check runs. Every other `sync.*` command starts with the same handshake (decision 18).

| # | Check | Severity | How |
|---|---|---|---|
| S0 | Identity handshake (above) | error, stops the run | catalog, HTTPS, gate |
| D1 | Schema: `alembic_version`, Postgres major, `vector` and `pg_trgm` versions equal on both sides; `current_setting('memes.revision')` equals the deployed image revision recorded by the last deploy | error | P2 preconditions, reused; revision and head come from the database, not from capabilities |
| D2 | Per-image digest manifests equal for the promotable set (new = 0, changed = 0). Image ids on prod that dev no longer promotes (flagged, cut, rejected, deleted on dev after promotion) are **left behind on prod (removals deferred)**: counts and ids, informational, never an error (decision 27) | error for new or changed; info for left behind | P2's digest module (`plan` in `--verify-only`) |
| D3 | Row counts per shipped table over the promotable set equal | error | counts, from D2's query |
| D4 | Global sets: every dev member is on prod with equal hash (concepts, popularity absorptions); members only prod has are left behind (informational) | error for missing or differing; info for left behind | comics and statistics are no longer shipped (decisions 22, 25) |
| D5 | Rows of a shipped table on prod that dev does not have: **left behind on prod (removals deferred)**, counts and ids | info (was error; becomes error again when removals are built) | key anti-join through the manifest |
| D6 | Class X tables on prod hold no rows that dev pipelines would have produced (`comics`, `comics_panels`, `statistics_snapshots`, duplicates, runs, ...): a promote that leaked scratch data shows here | error | registry class X list, `count(*)` |
| F1 | Every prod image row has a file whose name, size and (from the manifest) hash match **(id, filename, content_hash)**; every manifest file is on prod | error | P3 `list` and the manifest |
| F2 | Files on prod not in the manifest: **left behind on prod (removals deferred)**, count and names (they have no id) | info (was warn; the age of `RETIRED/` and the quarantine entries come back with the removals) | |
| F3 | `--deep-files`: sha256 of every prod file equals `content_hash` | error | remote `hash` verb, 7 to 66 s for general |
| F4 | **Non-regular entries under BASE_PATH**: symlinks, hard links with link count over 1, devices, sockets, FIFOs, directories other than the known set, files with odd modes, files not owned by the expected account | error | gate `list` reports `type`, `nlink`, `uid`, `mode` for every entry; the path of an anomaly is printed (needed to act), nothing else |
| P1 | Class P: counts on prod vs dev, age of `pull_state.json` (`finished_at`) and its `run_id`, last pull outcome, `ratings.mode` | warn when older than `sync.verify.pull_stale_days` (7); informational, not a difference | the 1 h freshness rule belongs to the deferred retire (decision 27), not to this check |
| P2 | Bug reports waiting on prod, bytes and oldest age | info / warn | gate `pull-list` |
| A1 | **Growth alarm, reader tables:** rows of `meme_impressions`, `search_history`, `search_not_found`, `meme_saves` and the byte size of each table against the previous reports and the pull state; a daily growth above `sync.verify.growth.*` (tracked, default 5x the trailing 7-day median, or an absolute ceiling) | warn, error at the hard ceiling | `pg_total_relation_size`, counts; trend from `<state dir>/runs/*/verify.json` |
| A2 | **Bug-report directory:** file count and bytes against the cap P5 enforces (the alarm fires at 80 % of it), growth per day | warn / error | gate `pull-list` metadata |
| A3 | **Disk per volume** (decision 23): free space on the database volume of this environment, the images volume, staging, dumps and container logs; thresholds per volume; reports each mount separately because the stacks share one VPS | warn at 20 % free, error at 10 % | gate `disk` verb (owner P5; prints the root timer's `disk.json`) |
| A4 | **Abuse caps in force:** an oversize `q` (over the cap, harmless GET) answers 422; with `--deep-net` also a declared `Content-Length` over the bug-report cap is refused at the proxy; the rate-limit headers or a 429 appear after a short burst of 40 cheap `GET /health` calls from the workstation (skippable) | error | HTTPS |
| S1 | Prod grants equal the registry-generated grants **for this environment's roles**: `backend_app` writes exactly the six P tables and no ratings table write path beyond its policy, `sync_writer` writes no P table and holds no privilege on any P table except the column-level `SELECT (image_id, query_hash, updated_at)` on `meme_text_ratings` (the one exception, `interfaces.md` h), no `TEMP`; `sync_reader` has `SELECT` on the P tables, `alembic_version` and `images(id)` only | error | `has_table_privilege`, `has_column_privilege` over every table and role against `sync.roles` |
| S2 | Prod roles can log in only as expected (`backend_app` cannot create objects; no superuser used by the backend; `pg_hba` has no `trust` for network sources; `CONNECT` on this database revoked from PUBLIC) | error | catalog |
| S3 | Backend answers `/health` with a fixed (non-driver) detail; `capabilities.stage == prod`, `capabilities.environment == --env`, `features` flags match the stage (comics, trends, ratings, statistics, uploads, history, admin all false) | error | HTTPS (the payload carries no revision or schema head; those are checked in D1 through the database) |
| S4 | Gate: `handshake` device ids equal, gate version equals the repo's, **two ssh accounts** present as designed: the verb account runs verbs only; the upload account is refused when it asks for a verb or a shell, and its sftp root is the staging chroot | error / warn | attempts that must fail, no write |
| S5 | Postgres connection count vs this stack's `max_connections`; HNSW/GIN indexes all present and valid (`pg_index.indisvalid`) | error | catalog |
| S6 | Latest backup of this environment's class P dump is younger than 36 h and restorable (size > 0, `pg_restore --list` succeeds), and its size is not smaller than the previous one by more than the rows it lost | warn | gate `fetch-backup` metadata |
| S7 | Stage policy (route tombstones) effective: a sample of `dev_only` routes answers 404 over HTTPS: `/api/admin/batches`, `/docs`, `/openapi.json`, `POST /api/uploads`, a comics route, `GET /api/diagnostics/statistics`, and, while `/reply` is off, a ratings route (decision 24) | error | HTTPS, no body |
| S8 | **Backend containment** (the compose project of this environment): BASE_PATH mounted read-only in the backend, read-only root file system, `cap_drop: ALL`, `no-new-privileges`, non-root user, no published port except loopback; and the backend refuses an image path outside the images folder (a test row is not needed: the check reads the container's configuration through the gate's `containment` verb (owner P5), and the behavioural refusal is covered by P5's test and the acceptance drill) | error | gate `containment`, which prints the file a root-run P5 timer wrote from `docker inspect` of this project only (the gate has no docker access) |

Dev-only conditions are never errors on dev. **Left behind (decision 27)** is always informational: D2, D5, D4 and F2 print it as "left behind on prod (removals deferred)" with counts and ids and it never changes the exit code. The report states for each check: result, severity, one line of detail (counts and ids only), and the command that fixes it. A check that finds a dev batch run active (`batch_runs` started rows) marks the digest checks `unstable: dev job running` as a warning instead of an error.
Exit codes: 0 all clear, 4 differences (any error), 3 only warnings with `--fail-on warn`, 2 could not run (precondition, connection, identity), 1 other.

## Output

Human: a table grouped by area, followed by the drift in report form (`interfaces.md` k): what a promote would `add` (new on dev), what it would `overwrite` (changed, with the digest groups), and what is `left_behind`, each with counts and the first N ids (`--top`, default 50). The same content goes to `runs/<runid>/report.json` (complete id lists) and `report.md` (verify section; when called by the driver it is the `verify` section of the combined report, which describes the state before an apply in a dry run). JSON (`--json`): `{env, ran_at, run_id, checks:[{id,status,severity,detail,fix,skipped_reason}], growth:{...}, summary}`; stored under `<state dir>/runs/<runid>/verify.json` when called by the driver, which is also the trend store of A1. No message text, no
file names except anomaly paths (F4), no connection strings.

## Tests

Report checks (decision 26): completeness (added, overwritten with groups, left behind, every check with severity and fix, ids in `report.json`, first N in `report.md`), reproducibility (frozen data gives the same report content and the same hash), redaction in the report files as for the rest; left behind is informational (an image flagged on dev after promotion shows as left behind and the exit code stays 0). Pure unit (check classification, severity roll-up, exit codes, S0 gating, growth arithmetic, redaction: a test feeds rows containing text and asserts it never reaches the output); integration with two databases (the P2 fixture): a clean state is green; each deliberate drift (changed tag, deleted image row on prod, missing derived row, extra row on prod and orphan file (both informational: left behind),
widened `backend_app` grant, invalid index, class X row on prod, wrong-environment database or marker, a table that grew tenfold, a symlink in a temporary BASE_PATH) turns exactly the right check red; LocalTransport for the file checks. HTTP checks against a test app started with `APP_STAGE=prod` on a free port. Not covered: the real VPS (the P7 rehearsal).

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | Report model, S0 gating, severities, exit codes, redaction, JSON schema, trend store, unit tests | P1, SC (P2-T2/T3) |
| T2 | D1 to D6 reusing P2's digest/plan modules | P2-T5 |
| T3 | F1 to F4, P2, A2 reusing P3 | P3-T4, P3-T5 |
| T4 | P1, A1, S1, S2, S5 against `sync.roles` | P5 roles |
| T5 | S3, S4, S6, S7, S8, A3, A4 (HTTP, gate, backup, containment) | P5a to P5d |
| T6 | Integration drift scenarios | T2 to T5 |

Size: medium. Mode: subagent-driven; one review (**read-only guarantee and redaction**: no write path on either connection, no content in output).

## Choices made by me (to confirm)

- Prod class D digests are read through `sync_writer` opened read-only (a fifth role `sync_verify` is not worth it yet).
- A4 and S7 make a handful of harmless requests to the public backend; `--skip A4,S7` turns them off. The burst check is 40 `GET /health` calls and is skippable.
- Growth thresholds are tracked settings; a stale pull (7 days) and a stale backup (36 h) are warnings. The alarm only reports; nothing prunes (decision 23).
- S8 trusts `docker inspect` through a gate verb rather than a docker socket on the workstation.

## P6 built (2026-11-14): `sync/verify/`, what is real and what is mock-only

The command is `python -m sync.verify` (`sync/verify/cli.py`; library entry `run_verify`, also used by `sync.run`). Modules: `model.py` (the `Check` model, severities, exit codes, the redaction of `detail`), `data.py` (D1 to D6 and the drift plan, reusing `sync.promote.digest` and `plan.build_plan` with no files gate), `files.py` (F1 to F4, P2, A2, reusing `sync.files.manifest` and `diff`), `runtime.py` (P1, A1, A3, S1, S2, S4, S5, S6, S8), `net.py` (S3, S7, A4 over an injectable `http`), `growth.py` (the trend store). Every check is a small function returning `{id, status ok|warn|error|skipped, severity, detail, fix, skipped_reason}`; `detail` is at most 80 characters of lower-case tokens (anything else is `[redacted]`), so no row content can reach `verify.json`, `report.json`, `report.md` or stdout; the only names printed are the F4 anomaly paths and the left-behind files of the drift (design). Both sides are read inside one `REPEATABLE READ, READ ONLY` snapshot after `SET default_transaction_read_only = on`; prod class D digests go through `sync_writer` in a read-only session, class P counts through `sync_reader` (tool `V` now also opens the reader; the handshake checks it when it is there).

| Check | State | Notes |
|---|---|---|
| S0 | built | handshake for tool V (H1 to H8, H10); a failure is exit 2, a failure report and no other check |
| D1 | built | alembic heads, server major, `vector` / `pg_trgm` versions, `memes.revision` against the dev git sha |
| D2, D3, D4, D5 | built | drift over the whole promotable set; left behind is `ok` with its count (informational, decision 27); new images held back by the completeness floor are counted (`floor_skipped`), not errors; a running dev batch downgrades the errors to warnings (`unstable:dev_job_running`) |
| D6 | built, approximate | neither prod role may SELECT a class X table (H3), so the rows come from `pg_stat_user_tables.n_live_tup` (statistics can lag a moment) |
| F1, F2, F4 | built | gate `list`, manifest diff, anomaly classes `not_regular`, `hardlinked`, `invalid_name`, `mode` (octal modes accepted: `sync.verify.files.modes`, default `644`; empty list: not checked), `owner` (the gate account uid from the handshake probe, when the gate reports one). Tested with `LocalTransport` against the real gate file |
| F3 | built | `--deep-files`: gate `hash` (rewrites only the gate's own `work/record.tsv`) plus a re-read of every dev file against `content_hash` |
| P1 | built | age of `pull_state.json`, ratings mode, last bug-report outcome; a warning after `sync.verify.pull_stale_days` |
| P2, A2 | built | gate `pull-list`; the age comes from the timestamp in the file name; the cap of A2 is `sync.pull.bug_reports.{max_files,max_total_mb}` (no other cap is defined), alarm at `sync.verify.bug_reports.alarm_pct` (80) |
| A1 | built | rows (reader counts) and `pg_total_relation_size` of the four reader tables plus bug-report files and bytes, trend from the last 30 `runs/*/verify.json` of the environment; see `growth.py` for the arithmetic |
| A3, S8 | built against the gate verbs `disk` / `containment`; **mock-only** | the shapes of the two JSON files are not fixed by any design: the shapes P6 understands are in `interfaces.md` g; another shape or a missing file is `skipped` (`facts_unusable`, `gate_facts_missing`) |
| S1, S2, S5 | built (catalog) | S1: `evaluate_role` for the writer and the reader and `runtime.evaluate_app` for `memes_<env>_app` through `has_table_privilege(<name>, ...)`; S2: PUBLIC `CONNECT`, `trust` in `pg_hba_file_rules` (skipped as `no_privilege` when the role cannot read it), the app role's attributes; S5: connections against `max_connections` (warn 80 %, error 95 %), HNSW / GIN / ivfflat indexes of dev present and valid on prod (not while `memes.state` is `seeding`). Exercised only on the fixture (superuser role), real grants first meet a real prod in the rehearsal |
| S4 | built for the gate facts (version equals the repo's file, `base` and `work` on one device); S4b (the two ssh accounts) is `skipped: account_probe_unavailable` unless a probe is injected | needs a real sshd |
| S6 | built against `fetch-backup --meta`; **mock-only** | no backup exists yet; no backup is `skipped: no_backup` |
| S3, S7, A4 | built against an injectable `http`; **mock-only** | `GET /api/diagnostics/health` and `/capabilities`, the 404 sample of S7, an oversize `q` on `/api/images`, and with `--deep-net` a declared `Content-Length` over the bug-report cap and a burst of 40 health calls; a missing `sync.prod.api_url` or an unreachable host is `skipped` |

Plugged into `sync.run` (`sync/run/cli.py`): `runners["verify"]` is a fourth runner. In the plan (dry run and the apply's re-plan) it is the planning part only: the handshake for tool V and the drift of prod against dev (`images`, the global sets, and `files` when the driver has a gate transport and the dev BASE_PATH) as the `verify` section of the combined document, without checks, so the section joins the combined plan hash (and a `files` entry that equals the files section's is expected). In the apply, step 4 runs the whole `sync.verify` with the run's id and `lock=False`, writes `runs/<runid>/verify.json`, and `verify_step()` maps its exit code to `run.json` (`ok`, or `failed` with `verify_checks_failed`; exit 4 of the data step still runs it and keeps the step failed with `promote_verify_phase`). The earlier `verify: not_built` placeholder is gone.

Decisions and deviations: the `severity` of a check is its class from the design table (`error`, `warn`, `info`), the `status` is the outcome; an informational finding is `status ok`. `--json PATH` writes the `verify.json` document of this design, not `report.json`; the stand-alone run also writes `report.json` / `report.md` into its `dry` run folder (which is never an input). A4's oversize `q` length (4000 characters, `net.OVERSIZE_Q_CHARS`) and the route of the declared-length probe are assumptions until P5a builds the caps.

---

# Part B: P7, runbooks, driver, docs and the acceptance run

## Intent

Someone (the owner at 11 pm, or an agent months from now) must be able to provision a prod environment, run the routine sync, deploy a release, recover from a failed promote and add a table without rereading six designs. P7 turns the epic into runbooks, one driver command that enforces the order,
the repository docs that point at them, and one end-to-end acceptance run on a throw-away VPS before the real prod. Everything is **per environment** (decision 17): one compose project and one Postgres each for `it`, `metal` and `general`, only Caddy shared.

## Deliverables

### 1. The driver: `python -m sync.run`

`python -m sync.run --env {metal,general,it} [--apply] [--first-seed] [--plan-hash H] [--yes] [--stop-after STEP] [--top N] [--json PATH]` plus every lifting flag of `interfaces.md` i (`--allow-mass-change N`, `--allow-shrink TABLE`, `--allow-growth`, `--allow-empty-source`, `--ignore-running-batches`), forwarded to the step that owns it (the dev running-batch refusal of step 0 is lifted only by `--ignore-running-batches`, never by `--yes`)

Steps, in order, each a library call into P2 to P6 (not a subprocess) so exit codes and plans are structured. **Pull and promote are one `sync.run`** (P2 review H1). The pull's snapshot was meant as the evidence the retire phase compares against; with removals deferred (decision 27) the pull stays step 1 for the fresh reader data but is not a precondition of the promote. The order is: handshake, pull, files, data, verify.
0. `handshake`: the identity handshake of decision 18 (`interfaces.md` e, H1 to H10) for every connection the run will use (database, role, `memes.environment`, capabilities `environment`, gate environment, `memes.state`, revision); the dev batch-run check (the run **refuses while a dev batch run is active**, `batch_runs` started rows; `--yes` does not lift it); the schema gate; a quick `verify` (S0 to S4).
1. `pull` (P4): reader activity and bug reports; writes `pull_state.json` with this run's id.
2. `files` (P3 promote): new and changed files, always dev's current bytes (decision 20), into prod through the upload account and the verb account; writes `files-promoted.json`.
3. `data` (P2): rows and global sets (add and overwrite only), gated by the files report on **(id, filename, content_hash)**.
4. `verify` (P6) with its result in the run folder.
Deferred (decision 27, not built): the retire phase of the data step and `files-retire` (P3); see P2 and P3, "Deferred phase: removals".

Run folder `<state dir>/runs/<runid>/` (one per environment; fields in `interfaces.md` a): `run.json` (steps, status, timings, flags, plan hash), `report.json` and `report.md` (the combined report, informational only: nothing downstream reads them), `files-promoted.json`, `committed.json`, `verify.json`. `pull_state.json` lives at the state directory root (P4 writes it; the driver records its `run_id` in `run.json`). Dry-run folders carry a `dry` marker, and no consumer ever accepts a dry-run file as input.

**Dry run** (default, decision 26): runs the planning part of every step and writes **one combined report** (`interfaces.md` k) with one section per step: for `pull` the rows to be added and replaced on dev, the ratings fingerprint and the bug reports to fetch; for `files` the files to be added and overwritten (the replaced-files warning count), the left-out problems and the files left behind; for `data` the images and rows to be added and overwritten (with the changed digest groups), the images skipped with reasons, the images left behind and each guard's decision with the flag that would lift it; for `verify` the drift
as of before the apply. Plus the handshake and precondition results, the estimated duration and bytes on the wire, and the combined **plan hash** with the exact `--apply --plan-hash` command. **Dependency note, printed in the report:** later sections depend on earlier steps being applied, so the `data` section of a dry run shows the images whose files are not yet on prod under `waiting_for_files` (they count in the expected totals and in the hash). The report goes to `report.json` / `report.md` in the run folder (with the `dry` marker) and a summary to stdout; the driver exits 0.
**The owner reads the dry-run report before every apply** (runbooks say so; the acceptance run checks it).
**`--apply`** re-plans, refuses (exit 3) if the plan hash differs from the one named by `--plan-hash` (if given), then shows the **banner** (`dev <env> (<dev db> @ localhost, read-only) -> prod memes_<env> as memes_<env>_sync_w via ssh alias memes-prod-<env>`) and the combined plan, and asks the owner to type `<env> <prod database name>` once. `--yes` skips only that typed
confirmation. The run stops at the first non-zero step and prints what is committed and what to run next (every step is resumable on its own); the apply's report carries a `result` block. `--stop-after STEP` is for rehearsal.
A lock file in the state directory prevents two drivers on one environment. Environments are promoted one at a time; the driver does not loop over environments.

**First seed** (`--first-seed`, P2 review H7): allowed while the prod database setting `memes.state` is `seeding` (set by `provision.sh` next to `memes.environment`, P5d; the owner flips it to `serving` after the index rebuild; there is no other marker). It is **resumable and idempotent**: a re-run computes the same manifest diff and continues by image batches (images with all their rows), with the index-less cost model but the same invariant order, skipping what already matches. No separate "wipe and restart" path is needed; the recovery runbook shows how the owner wipes by hand if wanted. After `serving`, `--first-seed` is refused.

Tests: unit tests of ordering and stop-on-error with fake steps; handshake mismatch stops before any step; refusal while a dev batch run is active; **report checks (decision 26):** the combined report has one section per step and every category, the dependency note and `waiting_for_files` appear in a dry run, the plan hash is reproducible and a change between dry run and apply makes `--apply --plan-hash` exit 3, the report holds ids and counts only (sentinel text from a note, a search text and a filename never appears); one integration scenario over the real modules and the two-database fixture (dry run, then apply with the printed hash; pull, files, data, verify; then a second run plans nothing; a killed run between files and data resumes; an image flagged on dev afterwards is reported as left behind and stays on prod).

### 2. Runbooks (`docs/runbooks/`, next to the existing `ingestion-pipeline.md`)

All runbooks take `<env>` as a parameter; compose commands use the project name `memes-<env>`; nothing is shared except what is marked "once for the VPS".

| Runbook | Covers |
|---|---|
| `prod-provisioning.md` | **Once for the VPS (4 GB or more):** Docker, firewall (22, 80, 443 only), the shared Caddy, the two ssh accounts (verb account with the gate, upload-only account chrooted to staging), directories and ownership (shared gid, setgid for bug reports), backup timer skeleton. **Per environment:** per-environment directory tree, env files from the examples, `docker compose -p memes-<env>` bring-up, roles SQL for that stack, `memes.environment` and `memes.state = seeding`, first migration, the keys of that environment (verb key, tunnel key, upload key), `authorized_keys` lines with `permitopen`/gate environment, the workstation side (`.env.<env>.sync`, the three ssh `Host` blocks `memes-prod-<env>`, `-sync`, `-up`, Windows ssh-agent), Caddy hostname, smoke tests, `python -m sync.doctor --env <env>`. Bring up and test **one environment at a time** |
| `prod-first-seed.md` | Empty prod environment: owner drops the HNSW/GIN indexes, `sync.run --first-seed` as a dry run first (read the report), then with `--apply --plan-hash` (resumable), owner rebuilds indexes (decision 8), sets `serving`, `verify --deep-files`, open the proxy hostname. Times from S2/S3/S4 as expectations. Order across environments: **`it` first (121 MB), then `metal`, then `general`**. A resume after a crash is the same command |
| `prod-routine-sync.md` | The sequence, what each step prints, how to read the dry run and the combined plan, **how to read the dry-run report** (added, overwritten, skipped with reason, left behind; the plan hash and the apply command; the owner always reads it before the apply), the guards and the exact flag that lifts each (`--allow-mass-change N`, `--allow-empty-source`, `--allow-shrink TABLE`, `--allow-growth`), how often, what to do when a step fails; the daily glance at the `verify` growth lines (A1 to A3); when to flip `ratings_mode` |
| `prod-release.md` | Deploy order per environment: additive migration as the owner role, **re-apply the roles SQL (idempotent) and run `verify` S1 before the backend restart** (P5 review H5), backend restart, then promote; image tagged by revision; rollback of a bad image (previous tag; why an additive migration makes it safe); a taxonomy/settings change as a code deploy |
| `prod-recovery.md` | Failed promote mid-way (resume); a promote that overwrote data it should not have (fix dev, run the dry run, read what would be overwritten, apply; there is no retire, quarantine or restore until removals are built, decision 27); wrong-environment alarm (handshake refused: what to check); VPS loss (rebuild from the repo, restore the class P dump of each environment, then promote, since the dump's ratings need the `images` rows); dev DB loss (restore dev, then pull); corrupted index (owner rebuild); disk full (which volume, what is safe to remove: logs, dumps, never BASE_PATH); a hostile-upload or symlink finding from F4 (isolate, never follow); wiping and restarting a first seed |
| `prod-credentials.md` | **Key rotation and a leaked credential**, per environment: rotating the verb ssh key, the upload-account key, `sync_reader`, `sync_writer` and `backend_app` passwords, `.env.<env>.sync`, the order that never leaves the workstation without access, and what to do first on a leak (revoke the key line, rotate, run `verify`, read the proxy log) |
| `prod-abuse-and-growth.md` | Reading the A1 to A4 alarms (decision 23), blocking a source at Caddy, tightening a cap, what a bug-report flood looks like and how the pull drains it, why nothing is pruned automatically (decision 11) and what the owner can decide by hand |
| `adding-a-table.md` | The checklist: model, migration, registry entry and class, `python -m sync.render --write`, digest exclusions, grants regeneration (and the release step that applies them), route policy if an endpoint, class P tables get a pull entry and a growth check, `backend_api.md`, tests that fail first |

A **runbook guard** in `tests/docs/` (the pattern of `test_rollout_runbook.py`): every `python -m sync.*` command line names a module that exists and options its parser defines; every file path mentioned exists; no runbook contains a secret-shaped string; **no runbook mentions a `PROD_*` variable or a hand-opened `ssh -L`** (decision 18); every command with `<env>` also carries a compose project or `--env`.

### 3. Repository docs

`CLAUDE.md`: a short "Dev and prod stages" section (the two stages, the commands, the state directory, the rule that **agents never get `environments/.env.<env>.sync`** or any prod credential, `DATABASE_URL_READONLY` only for dev; the batch-pipeline list gets the sync commands as manual-only); `docs/data-flow.md`: the sync arrows (no statistics, no comics);
`environments/Environments.md`: prod port convention per environment (loopback), what is prod vs dev; `backend_api.md`: capabilities (with `environment`), the prod 404 matrix (comics, statistics, ratings while `/reply` is off, uploads, history list, admin); `docs/security/admin-permissions-todo.md`: prod mitigation, reader write routes and their caps;
`docs/image-derived-data.md` and `docs/sync-registry.md` already linked; `docs/sync/` pages per tool (`promote.md`, `files.md`, `pull.md`, `verify.md`, `run.md`) generated or checked against `--help`. Follow-up tasks named by the P5 review (sanitising `tools/discovery_report` output and CSV formulas; ratings export on pulled data) are listed, not built.

### 4. Acceptance run (the exit criterion of the epic)

On a throw-away VPS (or a local VM with latency injected: `tc netem` for 40 ms and 1 % loss, bandwidth cap 20 Mbit/s) the owner and the agent walk the runbooks **literally**, one environment at a time (three compose projects `memes-it`, `memes-metal`, `memes-general`, each brought up, seeded and drilled before the next; at the end all three run together and memory, connections and disk are recorded, since they share one VPS):
1. Provision once-for-the-VPS and then `it` from `prod-provisioning.md`, no unrecorded steps; repeat the per-environment part for `metal` and `general` as the exercise requires.
2. First seed of `it` with the real data, then `metal`; time every step against the estimates (S2 to S4); interrupt one first seed halfway and resume it.
3. A routine `sync.run` with a planted change of each kind (new image, re-embed, edited note, an absorption, and a flagged image, which stays on prod and is reported as left behind). The owner **reads the dry-run report**, compares it with the planted changes (added, overwritten with the right digest groups, `waiting_for_files`, left behind), applies with the printed `--plan-hash`, and checks that the apply's result equals the report; a change made on dev between dry run and apply must make the apply refuse (exit 3). `verify` green apart from the informational left-behind lines.
4. A pull with synthetic reader activity created through the public site (saves, searches, a not-found, a bug report): it arrives on dev; with `sync.pull.ratings_mode: mirror` and the ratings routes enabled for the drill only, a rating made on dev survives the pull and the pull report lists it as kept. (The retire drill, a rating made on prod after the pull snapshot stopping the retire, waits for removals.)
5. Failure drills: kill the ssh tunnel during a batch and resume; kill the driver between files and data and resume; fill the staging disk; run the promote against a prod missing a migration (must refuse); try the dev-only routes and the dev-only writes with the `backend_app` role (must be refused).
6. **Security drills:** (a) a **hostile tar** through the upload account (absolute and `..` names, a symlink, a hard link, a device, an oversize member, too many members, duplicate and over-long names, a member with a name not in the manifest): every one refused, nothing becomes visible in BASE_PATH, staging is cleaned; (b) a **symlink planted in BASE_PATH** on the VPS: verify F4 turns red and the backend does not follow it; (c) the **backend containment** check S8, `touch` inside the backend container's images folder fails, and an image path outside the folder is refused; (d) the **upload account** cannot run a verb, cannot leave its chroot, cannot write BASE_PATH; (e) the **wrong-environment drill:** point metal's tracked `sync.prod.ssh_alias`/`local_port` at general's, or put general's password in `.env.metal.sync`: every tool exits 2 at the handshake with no write; (f) the **abuse drill:** a burst against the public site meets the rate limit, an oversize `q` and an oversize bug report are refused, and synthetic rows trip the A1/A2 growth alarm; (g) the routes of decisions 22, 24 and 25 answer 404 on prod.
7. **Backup and restore drill per environment:** restore each environment's class P dump into a scratch database of its own stack, then check the counts against the pull.
8. Leave a written acceptance report in `docs/superpowers/reviews/` with measured times and every deviation from the estimates, and only then plan the real prod (`general` last).

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | `sync.run` with fake-step unit tests, handshake step, banner, typed confirmation, combined report (via `sync/report.py`) | P2 to P4 interfaces |
| T2 | Driver integration scenario in CI (run-folder results, report reproducibility, first-seed resume) | P2 to P4 built |
| T3 | Runbooks (the table above), drafted early from the designs, corrected during the rehearsal | P5 |
| T4 | Runbook guard test | T3 |
| T5 | Repository docs (CLAUDE.md, data-flow, Environments, backend_api, security todo, `docs/sync/*`) | all |
| T6 | Acceptance run and report, including the security drills | everything |

Size: medium plus two interactive days (three environments). Mode: T1, T2, T4, T5 subagent-driven; T3 drafted by an agent, **corrected by the owner and the agent together during T6**; T6 is the owner's session with the controller running commands, never an unattended subagent. Review: one fresh subagent on the runbooks and the driver
(**can the documented commands destroy data or hit the wrong environment if followed literally; are confirmations and guards present**).

## Revision notes (2026-11-12, after the reviews of P2, P3 and P5 and decisions 17 to 25)

| Finding / decision | Action |
|---|---|
| 17: one stack per environment | verify is per environment; runbooks and acceptance are per environment and compose project; first-seed order kept; per-environment disk and backup checks |
| 18, 21: no `PROD_*`, tool-owned tunnel, handshake | connections rewritten; S0 gates the verify; handshake is step 0 of the driver; guard test forbids `PROD_*` and hand tunnels in runbooks; CLAUDE.md rule |
| 19: hardened tar, two accounts | S4 checks the accounts; F4 symlink/non-regular detection; S8 containment; hostile-tar, symlink and upload-account drills |
| 20: changed bytes always promoted | driver step 2 states it; F1 matches (id, filename, content_hash) |
| 22, 25: comics and statistics not shipped | removed from D4; new D6 (class X rows on prod); S7 sample includes comics and statistics routes; docs text updated |
| 23: abuse limits and growth alarm | A1 to A4, A3 per volume, `prod-abuse-and-growth.md` |
| 24: ratings routes off on prod | S7 expects 404 for a ratings route while `/reply` is off; P1 shows `ratings_mode`; acceptance enables ratings only for the drill |
| P2 H1: ratings lost between pull and retire | pull and promote in one `sync.run`; retire compares the pull fingerprint (acceptance drill); deferred with the retire, decision 27 |
| P2 H3: retire reads the plan | driver and P3 retire read `retired.json` of this run; the report is informational; dry-run files are never inputs (retire deferred, decision 27) |
| P2 H5: torn dev reads | verify reads in one snapshot transaction; unstable marking while a dev job runs |
| P2 H7: first seed not resumable | `--first-seed` resumable and idempotent while `seeding`; database setting `memes.state` from P2/P5 |
| P2 C2: `--allow-mass-retire` from habit | typed `<env> <database>` confirmation; `--plan-hash` binding (the counted retire form is deferred, decision 27) |
| Decision 26 (2026-11-12) | every operation is a dry run with a report: verify prints its drift in report form (added, overwritten, left behind), the driver writes one combined report with a dependency note and `waiting_for_files`, the owner reads it before every apply, report checks in the tests, runbooks and acceptance run |
| Decision 27 (2026-11-12) | driver order becomes handshake, pull, files, data, verify (no `--retire`, no counted retire confirmation, no `files-retire`); D2, D5, F2 and D4 treat left behind as informational; recovery runbook and acceptance drills drop the retire, quarantine and restore parts; the pull is no longer a precondition of the promote |
| Reconciliation 2026-11-12 | `interfaces.md` adopted: capabilities carry no revision or schema head (D1 reads them from the database), `memes.state` replaces the assumed marker, `committed.json` replaces `data-result.json`, `--plan-hash` replaces `--plan-id`, S1 states the one ratings column grant, `disk`/`containment` verbs owned by P5 and fed by a root timer, SC dependencies |
| P2 refusal while a dev batch run is active | handshake step refuses; `--yes` does not lift it |
| P5 H5: grants not re-applied after migration | release runbook step 2b and S1 before the restart |
| P5 M3: `/health` leaks driver text | S3 expects a fixed detail |
| P3 quarantine `restore` verb | named in `prod-recovery.md` (deferred with the removals, decision 27) |
| Key rotation, leaked credential | new `prod-credentials.md` |
| Own: P class counts alone do not show floods | trend store in `runs/*/verify.json` |
| P6 built (2026-11-14): `sync/verify/` | see "P6 built" above; `sync.run` step 4 and the plan's `verify` section are the real thing now (the next row's item (5) is superseded) |
| P7-T1 built (2026-11-14): `sync/run/` | The driver is `sync/run/cli.py` (`python -m sync.run`). Decisions made while building: (1) each step is an in-process call of the tool's own `run_pull` / `files promote run_promote` / `run_promote` with `lock=False` (the parent holds the one state-directory lock) and the child's argv built for the child's own parser; the child's document is collected through an `args.document_sink` hook in each tool's `_emit`. (2) A dry run runs the children in dry mode under the run's own `dry` folder and merges the sections (`mark_waiting_for_files`, dependency note, combined hash). (3) An apply first RE-PLANS the same way in a scratch `dry` folder (own run id, removed afterwards, because a folder with the `dry` marker never takes an apply report and the children must write the real run folder), compares the combined hash, pre-checks the guards with each tool's own `effective_blocking` (a wrong `--allow-mass-change N` or an un-lifted guard is exit 3 before ANY step writes, not after the files are already sent), asks the typed confirmation once (`<env> <prod database read back in step 0>`), then runs pull, files, data with `--apply --yes` and no child plan hash, sharing the run id. (4) One ssh tunnel for the whole run: the driver opens it and hands the children a session opener that reuses it; the children still open their own database sessions and run their own handshake steps. (5) Step 4 `verify`: `sync.verify` (P6) is not built; the plan carries one informational check `verify: skipped_with_reason not_built` (part of the hash), and the apply accepts the verify phase `sync.promote` runs at its end (`verify_step()` in `sync/run/cli.py` is the one swap point; exit 4 from the data step is a failed verify). (6) Stopping at the first failing step returns that step's exit code, names the completed steps and prints the same command (without `--plan-hash`) to resume; every step is resumable by design. Owner decisions 28 and 29 (README) apply: the pull section of the combined hash is structural, and the typed confirmation rule |

## Choices made by me (to confirm)

- Environment order for the first seed: `it`, `metal`, `general`; the first seed resumes by the same command while `seeding`.
- The driver calls library functions, not subprocesses, and promotes one environment per invocation; the dry-run report is read before every apply.
- The acceptance run targets a throw-away VPS or VM; the real prod environments are created only after its report, `general` last.
- Runbooks are tested by a guard for command and path validity, not by executing them.
- Growth and disk thresholds are tracked settings with the defaults above, to be tuned in the rehearsal.

## Risks and what is not settled

- The runbooks cannot be proven without T6; until then they are drafts built on loopback measurements.
- Everything that depends on the real sshd, the real network, the chroot and the real VPS (S3/S4 "not verified") is first exercised in T6.
- Three stacks on one 4 GB VPS may be tight while seeds and index rebuilds run; the acceptance run records it, and seeding one environment at a time is the mitigation.
- A rehearsal VPS costs a little money and two days of the owner's time; skipping it is possible and not recommended.

## Open for the owner

Whether to rent a throw-away VPS for T6 or use a local VM first (the chroot and sshd behaviour are only fully real on a VPS or a Linux VM). Confirm the first-seed resume policy (resume by the same command; no wipe mode). Not needed before the build.
