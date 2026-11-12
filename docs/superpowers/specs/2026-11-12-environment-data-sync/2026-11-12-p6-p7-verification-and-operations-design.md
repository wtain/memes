# P6 and P7: verification, drift report, runbooks and operations (design)

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (built), P2 (digests, verify phase), P3 (file manifest, report), P4 (pull state), P5 (capabilities, roles, stack), decisions log in `README.md`.
Two projects in one document because they share the rehearsal: **P6** is a read-only command that says whether prod equals dev; **P7** is the runbooks, the driver and the docs that make the whole epic operable, and the acceptance run on a throw-away VPS.

# Part A: P6, verification and drift report

## Intent

An independent, read-only answer to "is prod what dev says it should be, and is it healthy and safe?", runnable at any time, after every promote, and before a release. P2's verify phase checks only what P2 just wrote; P6 checks everything an operator would otherwise
have to remember: data, files, schema, privileges, health, freshness.

## Goals / non-goals / anti-goals

- Goals: one command and one report (human and JSON) with severity levels; checks for data, files, schema, grants, runtime and sync freshness; exit codes usable from a script; no write on either side; no row content in the output.
- Non-goals: repairing anything (it names the command that repairs it); monitoring and alerting (a single report is enough for a dev-initiated, manual process); checking dev's internal consistency (tests and `tools/rollout_preflight` do that).
- Anti-goals: a check that needs a writable connection; a report that prints search text, filenames of private data beyond ids needed to act, or credentials; a green report that skipped a check silently (every skipped check is listed as skipped, with the reason).

## Command

`python -m sync.verify --env {metal,general,it} [--json PATH] [--deep-files] [--skip CHECK,..] [--fail-on warn]`

Connections: dev read-only (`DATABASE_URL_READONLY`, the repo's read-only role); prod `PROD_PULL_DATABASE_URL` (role `sync_reader`, read only) for the class P and catalog checks, and `PROD_SYNC_DATABASE_URL` (role `sync_writer`) **opened read-only**
(`default_transaction_read_only`) for the class D digests, because `sync_reader` has no `SELECT` on D tables by design; the ssh host alias for files; `PROD_API_URL` for the runtime checks (`/health`, `capabilities`). Missing settings skip the dependent checks with a reason.

## Checks

| # | Check | Severity | How |
|---|---|---|---|
| D1 | Schema: `alembic_version`, Postgres major, `vector` and `pg_trgm` versions equal on both sides | error | P2 preconditions, reused |
| D2 | Per-image digest manifests equal for the promotable set (new, changed, retired = 0) | error | P2's digest module (`plan` in `--verify-only`) |
| D3 | Row counts per shipped table over the promotable set equal | error | counts, from D2's query |
| D4 | Global sets equal by key and hash (concepts, comics, absorptions, statistics) | error | |
| D5 | Prod has no row of a table that dev does not (orphan rows in D tables) | error | key anti-join through the manifest |
| F1 | Every prod image row has a file of the manifest size; every manifest file is on prod | error | P3 `list` and the manifest |
| F2 | Files on prod not in the manifest (orphans) and age of `RETIRED/` | warn | |
| F3 | `--deep-files`: sha256 of every prod file equals `content_hash` | error | remote `hash` verb, 7 to 66 s for general |
| P1 | Class P: counts on prod vs dev, age of `pull_state.json`, last pull outcome | warn when older than 7 days | informational, not a difference |
| P2 | Bug reports waiting on prod | info | gate `pull-list` |
| S1 | Prod grants equal the registry-generated grants: `backend_app` can write exactly the six P tables, `sync_writer` no P table, `sync_reader` nothing | error | `has_table_privilege` over every table and role against `sync.roles` |
| S2 | Prod role can log in only as expected (`backend_app` cannot create objects); no superuser connected by the backend | error | catalog |
| S3 | Backend answers `/health`; `capabilities.stage == prod`; `capabilities.revision` equals the image revision recorded by the last deploy; `schema_head` equals `alembic_version` | error | HTTPS |
| S4 | Gate: `handshake` device ids equal, free disk above threshold, gate version equals the repo's | error/warn | |
| S5 | Postgres connection count vs `max_connections` (S5 headline 6), HNSW/GIN indexes all present and valid (`pg_index.indisvalid`) | error | catalog |
| S6 | Latest backup of the class P tables is younger than 36 h and restorable (size > 0, `pg_restore --list` succeeds) | warn | gate `fetch-backup` metadata |
| S7 | `StageGate` effective: a sample of `dev_only` routes answers 404 over HTTPS (`/api/admin/batches`, `/docs`, `/openapi.json`, `POST /api/uploads`) | error | five requests, no body |

Dev-only conditions are never errors on dev. The report states for each check: result, severity, one line of detail (counts and ids only), and the command that fixes it (for example `python -m sync.pull` or the promote).
Exit codes: 0 all clear, 4 differences (any error), 3 only warnings with `--fail-on warn`, 2 could not run (precondition/connection), 1 other.

## Output

Human: a table grouped by area. JSON (`--json`): `{env, ran_at, checks:[{id,status,severity,detail,fix,skipped_reason}], summary}`; stored under `<state dir>/runs/<runid>/verify.json` when called by the driver. Neither carries message text, filenames (ids only), connection strings or hostnames beyond what the
operator already typed.

## Tests

Pure unit (check classification, severity roll-up, exit codes, redaction: a test feeds rows containing text and asserts it never reaches the output); integration with two databases (same fixture as P2): a clean state is green; each deliberate drift (changed tag, deleted image row, missing derived row, extra row on prod, orphan, widened `backend_app` grant, invalid index) turns exactly the right check red;
LocalTransport for the file checks. HTTP checks against a test app instance started with `APP_STAGE=prod` on a free port. Not covered: the real VPS (the P7 rehearsal).

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | Report model, severities, exit codes, redaction, JSON schema, unit tests | P1 |
| T2 | D1 to D5 reusing P2's digest/plan modules | P2 T4 |
| T3 | F1 to F3 and P2 (bug reports) reusing P3 | P3 T4, T5 |
| T4 | P1 check, S1 to S2 grants against `sync.roles`, S5 | P5d-T1 |
| T5 | S3, S4, S6, S7 (HTTP, gate, backup) | P5a, P5d |
| T6 | Integration drift scenarios | T2 to T5 |

Size: medium. Mode: subagent-driven; one review (**read-only guarantee and redaction**: no write path on either connection, no content in output).

## Choices made by me (to confirm)

- Prod class D digests are read through `sync_writer` opened read-only (an alternative is a fifth role `sync_verify` with `SELECT` on D tables; not worth a role yet).
- `S7` makes five harmless requests to the public backend; `--skip S7` turns it off.
- Severities as in the table; a stale pull (7 days) is a warning, a stale backup (36 h) is a warning.

---

# Part B: P7, runbooks, driver, docs and the acceptance run

## Intent

Someone (the owner at 11 pm, or an agent months from now) must be able to provision prod, run the routine sync, deploy a release, recover from a failed promote and add a table without rereading six designs. P7 turns the epic into runbooks, one driver command that
enforces the order, the repository docs that point at them, and one end-to-end acceptance run on a throw-away VPS before the real prod.

## Deliverables

### 1. The driver: `python -m sync.run`

The order is a contract today, enforced only by freshness files. The driver makes it one command with one confirmation:

`python -m sync.run --env {metal,general,it} [--apply] [--retire] [--yes] [--stop-after STEP]`

Steps, in order, each a library call into P2 to P6 (not a subprocess shell-out) so exit codes and plans are structured:
1. `preflight`: connections, roles, schema gate (P2 phase 0), `verify --skip F3`-style quick checks (P6 S1 to S4).
2. `pull` (P4): reader activity and bug reports.
3. `files` (P3 promote): new and changed files to prod.
4. `data` (P2): rows, global sets; `--retire` includes the retire phase.
5. `files-retire` (P3 retire) when `--retire`.
6. `verify` (P6) with the result written to the run folder.

Dry run is the default: it runs the planning part of every step and prints one combined plan (counts, bytes, retire list size, guard decisions), writes the run folder `<state dir>/runs/<runid>/` and exits. `--apply` shows the combined plan, asks to type the environment name once (or `--yes`),
and runs the steps in order; it stops at the first non-zero step and prints exactly what is committed and what to run next (every step is resumable on its own). A lock file in the state directory prevents two drivers on one environment. Environments are promoted one at a time; the driver does not loop over environments.

Tests: pure unit tests of ordering and stop-on-error with fake steps; one integration scenario over the real modules and the two-database fixture (the epic's end-to-end test in CI: pull, files, data, retire, verify, then a second run that plans nothing).

### 2. Runbooks (`docs/runbooks/`, next to the existing `ingestion-pipeline.md`)

| Runbook | Covers |
|---|---|
| `prod-provisioning.md` | VPS prerequisites (Docker, firewall: 22, 80, 443 only), users (`memesync`, deploy), directories and ownership, ssh config and key restrictions with the gate (host key pinned, `BatchMode`, no ControlMaster on Windows), env files from the examples, compose bring-up, Caddy hostnames, roles SQL per database, first migration, backup timer, smoke tests |
| `prod-first-seed.md` | Empty prod: owner drops the HNSW/GIN indexes, `sync.run --first-seed`, owner rebuilds indexes (decision 8, `maintenance_work_mem` 1 GB), `verify --deep-files`, open the proxy. Times from S2/S3/S4 as expectations. Order across environments: **`it` first (121 MB), then `metal`, then `general`** (my call: smallest first) |
| `prod-routine-sync.md` | The sequence, what each step prints, how to read the dry run, what the guards mean and the exact flags to lift each one, how often (the owner's cadence), what to do when a step fails |
| `prod-release.md` | The deploy order of P5 (migration additive, backend restart, then promote), rollback of a bad image (previous tag; why an additive migration makes it safe), a taxonomy/settings change as a code deploy |
| `prod-recovery.md` | Failed promote mid-way (resume), accidental mass retire (reseed from dev; reader rows survive), VPS loss (rebuild from the repo, restore class P from the newest dump, full promote), dev DB loss (restore dev, then pull), corrupted index (owner rebuild), disk full, key rotation, a leaked credential |
| `adding-a-table.md` | The checklist: model, migration, registry entry, `python -m sync.render --write`, digest exclusions, grants regeneration, route policy if an endpoint, `backend_api.md`, tests that will fail first |

A **runbook guard** in `tests/docs/` (the pattern of `test_rollout_runbook.py`): every `python -m sync.*` command line in a runbook names a module that exists and options its parser defines; every file path mentioned exists; no runbook contains a secret-shaped string.

### 3. Repository docs

`CLAUDE.md`: a short "Dev and prod stages" section (the two stages, the six commands, the state directory, the rule that agents never get `PROD_*` URLs, `DATABASE_URL_READONLY` only for dev), the batch-pipeline list gets the sync commands as manual-only; `docs/data-flow.md`: the sync arrows;
`environments/Environments.md`: prod port convention (dev + 1000, loopback), what is prod vs dev; `backend_api.md`: capabilities, the prod 404 matrix, statistics read-only; `docs/security/admin-permissions-todo.md`: prod mitigation, history route, reader write routes; `docs/image-derived-data.md` and `docs/sync-registry.md` already linked; `docs/sync/` pages per tool (`promote.md`, `files.md`, `pull.md`, `verify.md`, `run.md`) generated or checked against `--help`.

### 4. Acceptance run (the exit criterion of the epic)

On a throw-away VPS (or a local VM with latency injected: `tc netem` for 40 ms and 1 % loss, bandwidth cap 20 Mbit/s) the owner and the agent walk the runbooks **literally**:
1. Provision from `prod-provisioning.md`, no unrecorded steps.
2. First seed of `it` with the real data, then `metal`; time every step against the estimates (S2 to S4) and record them.
3. A routine sync with a planted change of each kind (new image, re-embed, edited note, flagged image, deleted comics) and `verify` green.
4. A pull with synthetic reader activity created through the public site (saves, searches, a not-found, a bug report): it arrives on dev; a rating of an image retired on prod survives on dev.
5. Failure drills: kill the ssh tunnel during a batch and resume; kill the driver between files and data and resume; fill the staging disk; run the promote against a prod missing a migration (must refuse); try the dev-only routes and the dev-only writes with the `backend_app` role (must be refused).
6. A restore drill of the class P dump into a scratch database.
7. Leave a written acceptance report in `docs/superpowers/reviews/` with measured times and every deviation from the estimates, and only then plan the real prod (`general` last).

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | `sync.run` with fake-step unit tests | P2 to P4 interfaces |
| T2 | Driver integration scenario in CI | P2 to P4 built |
| T3 | Runbooks 1 to 6 (drafted early from the designs, corrected during the rehearsal) | P5 |
| T4 | Runbook guard test | T3 |
| T5 | Repository docs (CLAUDE.md, data-flow, Environments, backend_api, security todo, `docs/sync/*`) | all |
| T6 | Acceptance run and report | everything |

Size: medium plus an interactive day. Mode: T1, T2, T4, T5 subagent-driven; T3 drafted by an agent, **corrected by the owner and the agent together during T6**; T6 is the owner's session with the controller running commands, never an unattended subagent. Review: one fresh subagent on the runbooks and the driver
(**can the documented commands destroy data if followed literally; are confirmations and guards present**).

## Choices made by me (to confirm)

- Environment order for the first seed: `it`, `metal`, `general`.
- The driver calls library functions, not subprocesses, and promotes one environment per invocation.
- The acceptance run targets a throw-away VPS or VM; the real prod is created only after its report.
- Runbooks are tested by a guard for command and path validity, not by executing them.

## Risks and what is not settled

- The runbooks cannot be proven without T6; until then they are drafts built on loopback measurements.
- Everything that depends on the real sshd, the real network and the real VPS (S3/S4 "not verified") is first exercised in T6.
- A rehearsal VPS costs a little money and a day of the owner's time; skipping it is possible and not recommended.

## Open for the owner

Whether to rent a throw-away VPS for T6 or use a local VM first. Not needed before the build.
