# Dev/prod data sync: project tracker

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/README.md

Created 2026-11-12. Each project has its own design spec (written when the project is picked up, named
`YYYY-MM-DD-<id>-<name>-design.md` and linked below), then an implementation plan, then implementation. Project status uses the spec
lifecycle values (`draft`, `approved`, `planned`, `implementation`, `done`), with `not started` before a spec exists. Spikes use
`open`, `in progress`, `done` or `dropped`.

## Working rules

- **Worktree:** `.claude/worktrees/env-data-sync`, branch `worktree-env-data-sync`, created from local `main` (7da0ff82).
- **Merging to `main` and every database migration need the owner's explicit confirmation**, per project.
- **Ambiguities** are brainstormed with the owner, not guessed.
- **Live databases:** read-only role (`DATABASE_URL_READONLY`) only, run by the controller; never hand a subagent the main
  `DATABASE_URL`. Prod credentials are never written to the repo.
- **Ports:** never bind the metal, general or it ports; use a free port and `ocrdb_test` for any test backend.
- **Review:** one review iteration per project by a fresh subagent, saved to `docs/superpowers/reviews/`.

## Spikes

| ID | Spike | Question answered | Status | Output |
|---|---|---|---|---|
| S1 | Data classification | Which table and disk asset has which origin, and is it needed on prod? | done (draft; all open questions answered 2026-11-12, see the decisions log in README.md) | `2026-11-12-s1-data-classification.md` |
| S2 | Size measurement | Row counts and bytes per table, disk of BASE_PATH, so the prod disk can be sized and the trimming gain is known | done (2026-11-12; read-only role, run by the controller) | `2026-11-12-s2-size-measurement.md` |
| S3 | Sync technique | Per table: `COPY` with upsert over an SSH tunnel vs `pg_dump` subset vs logical replication; index drop/rebuild vs keep, measured; incremental change detection; how to carry deletions | done (2026-11-12; ADR status PROPOSED until the owner confirms; throwaway containers, synthetic data, no real SSH or network) | `docs/adr/adr-2026-11-12-dev-prod-sync-technique.md` |
| S4 | File transfer | rsync (or equivalent) from Windows over SSH; manifest by `content_hash`; resumability; row/file consistency | done (2026-11-12; ADR status PROPOSED; loopback container, synthetic files, no real network or VPS) | `docs/adr/adr-2026-11-12-dev-prod-file-sync.md` |
| S5 | Prod topology and roles | Prod DB roles (sync writer, backend, read-only), which backend features are switched off on prod, how prod is configured per env | done (2026-11-12; read-only code analysis, owner questions answered 2026-11-12) | `2026-11-12-s5-prod-topology-and-roles.md` |

## Projects (provisional, refined after S1 to S5)

| ID | Project | Delivers | Depends on | Status | Spec / plan |
|---|---|---|---|---|---|
| P0 | Epic charter and tracker | This folder | none | draft | `README.md`, `tracker.md` |
| P1 | Sync registry | The S1 classification as code (origin, ship/skip, dependency order, key, per table) plus a test that fails on an unclassified table | S1 | implementation (2026-11-12; built, reviewed, fixed; not merged, owner confirms the merge). Verified: `pytest tests/sync` 19 passed, `tests/docs` 15 passed, 18 mutations caught. Not verified: CI run | `2026-11-12-p1-sync-registry-design.md`, review `docs/superpowers/reviews/2026-11-12-p1-sync-registry-review.md` |
| P2 | Promote data dev to prod | Dry-run-first command: dev-origin tables for the active corpus, ids preserved, schema-version gate, row counts verified | P1, S3 | draft (2026-11-12, awaiting owner review) | `2026-11-12-p2-promote-data-design.md` |
| P3 | Promote files dev to prod | Image file sync driven by the promoted rows, with row/file consistency check and removal of retired files | P1, S4 | draft (2026-11-12, awaiting owner review) | `2026-11-12-p3-promote-files-design.md` |
| P4 | Pull reader activity prod to dev | Pull of saves, impressions, search history, not-found, ratings, uploads and bug reports; idempotent, watermarked | P1, S3 | draft (2026-11-12, awaiting owner review) | `2026-11-12-p4-pull-reader-activity-design.md` |
| P5 | Prod provisioning and hardening | First seed of an empty prod, trimmed schema usage, prod config, admin features off, roles | S2, S5, P2 | draft (2026-11-12, awaiting owner review; sub-deliveries P5a backend stage, P5b web, P5c Android, P5d VPS stack and roles) | `2026-11-12-p5-prod-provisioning-and-hardening-design.md` |
| P6 | Verification and drift report | Read-only compare of dev-origin data between stages (counts, hashes), report in the style of `tools/discovery_report` | P2, P3 | draft (2026-11-12, awaiting owner review) | `2026-11-12-p6-p7-verification-and-operations-design.md` (Part A) |
| P7 | Runbooks and docs | Runbook per direction, update `CLAUDE.md`, `docs/data-flow.md`, `docs/image-derived-data.md`, `environments/Environments.md` | all | draft (2026-11-12, awaiting owner review; ends with the acceptance run on a throw-away VPS) | `2026-11-12-p6-p7-verification-and-operations-design.md` (Part B) |

## Backlog / unscheduled

From the P5 to P7 designs:

- The P3 gate gains the verb `fetch-backup` (read-only: newest class P dump and its metadata), used by the pull and by P6 check S6; P4 already adds `pull-list`, `pull-tar`, `delete-verified`.
- P5d-T2: verify on a PostgreSQL 18 container that `MAINTAIN` allows `REINDEX`, that `ON DELETE CASCADE` into `meme_text_ratings` works without a `DELETE` grant for `sync_writer`, and the unprivileged index-rebuild path, before the roles are final.
- Caddy rate limiting may need a plugin; otherwise rate limits stay in the app and the deploy README says so.
- Open owner items that block nothing: hostnames and domain, VPS provider and size, whether the web build is served by the same Caddy, whether to rent a throw-away VPS for the acceptance run.

From the P3 and P4 designs:

- P5 grants and provisioning: `sync_reader` also needs `SELECT (id)` on `images` (P4 keeps ratings of images retired on prod); the `memesync` unix user, STAGING
  and RETIRED on the same filesystem as BASE_PATH, the gate script `deploy/prod/memesync-gate.sh` installed as the forced command; the backend and the gate must agree on file ownership.
- A driver `python -m sync.run --env X` that runs pull, files promote, data promote, retire (data, then files) in the right order with one confirmation; the order is a contract today,
  enforced only by the freshness files (`pull_state.json`, `files-promoted.json`). Candidate for P7.
- Impression pull by watermark (append-only) with a periodic full mirror, once `meme_impressions` approaches millions of rows (S3 Q7).
- Interface files shared across P2/P3/P4 live in the state directory `Storage/sync-state/<env>/` (git-ignored): `pull_state.json`, `runs/<runid>/{plan.json,files-promoted.json}`, the file ledger and the bug-report ledger.

From S4 and S5 (details in the ADR and the S5 spec):

- The backend has no authentication; of 90 routes 31 stay on prod and 59 are disabled by a default-deny stage gate
  (`APP_STAGE=prod`). New work items P5b (frontend) and P5c (Android) for hiding blocked pages.
- `popularity_absorptions` feeds the reader-facing trending and popularity breakdown routes; decide whether to ship it as D.
- `GET /api/history` exposes every reader's search text; proxy logs must drop query strings; `statistics` writes on GET (disable on prod).
- gunicorn `-w 4` with pool 10+20 can reach 120 connections against `max_connections=100`; rate limiters need proxy headers.
- Windows `ssh.exe` stdin is capped near 12.5 MB/s for pipes; ControlMaster does not work on Windows OpenSSH; `scp`/`sftp` leave
  truncated files under the final name when killed, so P3 stages to `.part`, renames, verifies, and sweeps staging on start.
- WSL rsync, a pure-Python SFTP client and real-network bandwidth were not evaluated.

From S3 (details in the ADR):

- Order of a sync run: pull prod's reader data first, then promote. Deleting an image on prod cascades to `meme_text_ratings` (the
  only reader table with a foreign key to `images`), so the pull must keep ratings of retired images and filter out ratings of
  images missing on dev (the foreign key would fail the pull).
- Embedding tables carry no model name or digest (only the meme-profile ones do), so model parity cannot be checked in the
  database; decide how P2 asserts it (settings comparison or a version stamp).
- Digest must also cover `image_description_feedback` and the `stale` flag.
- Prod `maintenance_work_mem` of at least 256 MB to 1 GB for HNSW builds (149 s at the 64 MB default vs 30 s, 43k x 1024d).
- Plain `REINDEX` of an HNSW index blocks readers; queries without the index are about 11x slower. Rebuild only in a window, or
  use a concurrent build.
- Not verified in S3: a real SSH tunnel and Windows OpenSSH behaviour, network latency, realistic lemma text for the GIN cost,
  comics/concepts/feedback tables, backend latency during a promote. Candidates for the P2 design or a short follow-up spike.

- Deletions and retirements: an image flagged, deleted or cut on dev must disappear from prod (tombstones or a full diff).
- Ratings keyed by `(query_hash, image_id)` are moved to the cut on a recut approve on dev; a later pull from prod must not
  resurrect the old pair.
- Per-env asymmetries: `it` has no tagging vocabulary, profiles or tagging jobs; tagging YAML only for metal and general.
- Trends (`trend_sources`, `trends_run_results`): where does the batch run, dev or prod?
- Tracked evaluation data (`evaluation/gold`) is keyed by dev image ids: dev-only, but the pull of ratings feeds it.
- Backups of prod-origin data on the VPS (the only copy of the readers' activity).
