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
| S1 | Data classification | Which table and disk asset has which origin, and is it needed on prod? | in progress (draft written 2026-11-12, awaiting owner answers to the open questions; S2 added Open question 10) | `2026-11-12-s1-data-classification.md` |
| S2 | Size measurement | Row counts and bytes per table, disk of BASE_PATH, so the prod disk can be sized and the trimming gain is known | done (2026-11-12; read-only role, run by the controller) | `2026-11-12-s2-size-measurement.md` |
| S3 | Sync technique | Per table: `COPY` with upsert over an SSH tunnel vs `pg_dump` subset vs logical replication; index drop/rebuild vs keep, measured; incremental change detection; how to carry deletions | done (2026-11-12; ADR status PROPOSED until the owner confirms; throwaway containers, synthetic data, no real SSH or network) | `docs/adr/adr-2026-11-12-dev-prod-sync-technique.md` |
| S4 | File transfer | rsync (or equivalent) from Windows over SSH; manifest by `content_hash`; resumability; row/file consistency | open | Decision record |
| S5 | Prod topology and roles | Prod DB roles (sync writer, backend, read-only), which backend features are switched off on prod, how prod is configured per env | open | Spec input for P5 |

## Projects (provisional, refined after S1 to S5)

| ID | Project | Delivers | Depends on | Status | Spec / plan |
|---|---|---|---|---|---|
| P0 | Epic charter and tracker | This folder | none | draft | `README.md`, `tracker.md` |
| P1 | Sync registry | The S1 classification as code (origin, ship/skip, dependency order, key, per table) plus a test that fails on an unclassified table | S1 | not started | |
| P2 | Promote data dev to prod | Dry-run-first command: dev-origin tables for the active corpus, ids preserved, schema-version gate, row counts verified | P1, S3 | not started | |
| P3 | Promote files dev to prod | Image file sync driven by the promoted rows, with row/file consistency check and removal of retired files | P1, S4 | not started | |
| P4 | Pull reader activity prod to dev | Pull of saves, impressions, search history, not-found, ratings, uploads and bug reports; idempotent, watermarked | P1, S3 | not started | |
| P5 | Prod provisioning and hardening | First seed of an empty prod, trimmed schema usage, prod config, admin features off, roles | S2, S5, P2 | not started | |
| P6 | Verification and drift report | Read-only compare of dev-origin data between stages (counts, hashes), report in the style of `tools/discovery_report` | P2, P3 | not started | |
| P7 | Runbooks and docs | Runbook per direction, update `CLAUDE.md`, `docs/data-flow.md`, `docs/image-derived-data.md`, `environments/Environments.md` | all | not started | |

## Backlog / unscheduled

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
