# Epic: data sync between dev and prod stages

Status: draft
Originates from: user request, 2026-11-12 (brainstorming session)
Follow-ups: project design specs are listed in `tracker.md` and written when each project is picked up.

Each environment (`metal`, `general`, `it`) is to run as two instances: a **dev** instance on the owner's workstation, where
ingestion, tagging, descriptions, deduplication and every other data pipeline run, and a **prod** instance on a VPS, with its own
Postgres, backend and disk. The data is an asset, not only the code and schema. This epic defines which data lives where, which
stage it originates on, and the scripts that move it between the stages.

This folder is the epic: charter in this file, project tracker in `tracker.md`, one spec per spike or project. The first deliverable
is the classification in `2026-11-12-s1-data-classification.md`.

## Intent

Let the owner curate and enrich on dev, roll the result out to prod, and bring the readers' activity from prod back to dev, without
either stage overwriting data the other stage owns, and without spending the VPS disk on pipeline scratch data.

## Decisions made (by the owner, in brainstorming)

- **Topology.** Prod is a separate machine (a VPS) with its own Postgres, backend and disk. All three environments get a prod
  instance.
- **Transport.** Dev initiates every sync over SSH. Prod never connects to the workstation, so "prod to dev" is a pull by dev.
- **Prod holds the full active corpus** of image files on its disk.
- **Image identity.** Image rows are created on dev only. Promotion keeps the dev-created UUIDs (no re-keying). No schema change is
  needed for this: `images.id` has a client-side default only (`default=uuid.uuid4`), so inserting explicit ids just works.
- **Curation happens on dev only.** Prod only collects reader activity. Hence every table has exactly one origin stage and data flows
  away from its origin only. There is no two-way merge.
- **`meme_text_ratings` are reader activity**: prod-origin, pulled to dev.
- **Activity made on dev is disposable** (2026-11-12). Reader-activity tables on dev are a mirror of prod's: a pull may replace
  them, so no stage marker or id merge is needed. Dev-made activity is never pushed to prod.
- **Indexes are built on prod**, never shipped (a data copy carries rows only). Whether to drop and rebuild them around a bulk
  load is decided by measurement in S3.
- **Prod has fewer admin capabilities** than dev (details in a later project).
- **Process.** Separate worktree (`env-data-sync`); the owner confirms every merge to `main` and every database migration;
  ambiguities are brainstormed, not guessed; agent-driven workflows for complex tasks.

## Decisions I made (guesses, to be confirmed in the project designs)

- **Prod receives only `active`, unflagged images** and their derived rows. `pending`, `rejected` and `cut_proposed` rows and files
  never leave dev.
- **Prod never registers images itself.** (Uploads are disabled on prod, decision 9.)
- **`images.popularity` is dev-origin and promoted as a value, together with `popularity_absorptions`** (decision 1).
- **Dev-only scratch is never shipped** (duplicate candidates and clusters, run tracking, processing markers, ingestion inbox,
  `excluded/`, `rejected/`, `pre-cuts/`, `converted_originals/`, backups, evaluation scratch).
- **Sync tooling shape**: dry-run first, table classification kept as code with a test that fails when a table is unclassified
  (same idea as `tests/docs/test_image_derived_data_inventory.py`), schema (alembic head) must match on both ends, and a
  prod-origin table can never be written by a dev-to-prod command. To be validated in spike S4.
- **Embeddings are model-bound.** Promotion carries the vectors, not a rebuild on prod; prod never runs the ML pipelines.

## Decisions log (owner answers, 2026-11-12, after S1 to S5)

| # | Question | Decision |
|---|---|---|
| 1 | `popularity_absorptions` | Dev-origin, **shipped to prod as class D** so trending (30 d) and the popularity breakdown work. (The code shows only dev jobs write it; saves and impressions do not change popularity.) |
| 2 | Retiring an image on dev | **Removed from prod** on the next promote (row and file), behind a mass-delete guard; reader rows are kept (ratings via a special rule, because they cascade) |
| 3 | Meme-from-text on prod | **Off for now** (like `it`: `meta.available: false`); no Ollama on the VPS |
| 4 | Statistics on prod | **Serve a promoted snapshot**: `statistics_snapshots` is class D, the prod route is read-only and never recomputes |
| 5 | Concepts / trends | **Concepts shipped (class D), trends hidden** (class X, `trends_batch` stays on dev) |
| 6 | Exposure | **Public internet**, no app login. Consequences: the history route and uploads are disabled on prod (below), reader write routes need rate limits, proxy logs must drop query strings |
| 7 | Postgres layout | **One cluster, three databases** on the VPS (cluster-wide roles with per-database grants; watch `max_connections`) |
| 8 | Index rebuild | **Owner step run locally on the VPS** for drop and rebuild; the promote tool (`sync_writer`) only inserts into existing indexes and may `REINDEX` |
| 9 | Uploads on prod | **`POST /api/uploads` disabled on prod**; phone and web upload to dev. No prod inbox, so P4 pulls no inbox files |
| 10 | `GET /api/history` | **Disabled on prod**; prod still records history and impressions for the pull |
| 11 | Retention on prod | **Keep everything**; prod is the system of record for reader data, no pruning |
| 12 | Android | **Add `GET /api/diagnostics/capabilities`**; web and Android read it to hide blocked screens (one app release) |
| 13 | Bug reports | **Kept on prod, size- and rate-limited**; dev pulls the files and prod copies are deleted only after a verified pull |
| 14 | Comics on prod | **Built only, with a shipped built image** (state `built` and the built image is promotable); drafts stay on dev; panel image links blanked when the source image is not shipped (P2 design) |
| (earlier) | Admin pages on prod | Off, by the default-deny stage gate of S5 (59 of 90 routes) |

Calls I made without asking (to be confirmed in the project designs): pull and promote are run by hand, pull first; prod backs up
the reader-activity tables nightly with `pg_dump` of those tables (the only copy); the cadence of the pull is the owner's.

## Goals / non-goals / anti-goals

- Goals: a written ownership map of every table and disk asset; repeatable, resumable, verifiable sync commands in both
  directions; a prod disk that holds only what prod serves; a way to check that prod equals dev for dev-origin data.
- Non-goals: running pipelines on prod; two-way merge of curation; live replication; changing the schema for identity.
- Anti-goals: a sync that can silently overwrite or delete prod-origin data; a promote that leaves rows without their files (or
  the reverse, since `unregister_deleted_images` deletes rows whose files are gone); secrets or the main `DATABASE_URL` in scripts.

## Open questions (for the owner, in the S1 review)

See the "Open questions" section of the S1 spec.
