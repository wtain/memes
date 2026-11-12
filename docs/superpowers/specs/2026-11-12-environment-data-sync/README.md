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
- **Prod has fewer admin capabilities** than dev (details in a later project).
- **Process.** Separate worktree (`env-data-sync`); the owner confirms every merge to `main` and every database migration;
  ambiguities are brainstormed, not guessed; agent-driven workflows for complex tasks.

## Decisions I made (guesses, to be confirmed in the project designs)

- **Prod receives only `active`, unflagged images** and their derived rows. `pending`, `rejected` and `cut_proposed` rows and files
  never leave dev.
- **Prod uploads** (`POST /api/uploads` on prod) land in prod's inbox as files; dev pulls those files into its own inbox and they
  re-enter through normal ingestion. Prod never registers images itself.
- **`images.popularity` is dev-origin and promoted as a value.** The popularity absorptions are written only by dev pipelines and
  reviews, and saves do not change it (recon), so prod needs only the cached number, not `popularity_absorptions`. This corrects
  my earlier chat guess of "recompute after promote".
- **Dev-only scratch is never shipped** (duplicate candidates and clusters, run tracking, processing markers, ingestion inbox,
  `excluded/`, `rejected/`, `pre-cuts/`, `converted_originals/`, backups, evaluation scratch).
- **Sync tooling shape**: dry-run first, table classification kept as code with a test that fails when a table is unclassified
  (same idea as `tests/docs/test_image_derived_data_inventory.py`), schema (alembic head) must match on both ends, and a
  prod-origin table can never be written by a dev-to-prod command. To be validated in spike S4.
- **Embeddings are model-bound.** Promotion carries the vectors, not a rebuild on prod; prod never runs the ML pipelines.

## Goals / non-goals / anti-goals

- Goals: a written ownership map of every table and disk asset; repeatable, resumable, verifiable sync commands in both
  directions; a prod disk that holds only what prod serves; a way to check that prod equals dev for dev-origin data.
- Non-goals: running pipelines on prod; two-way merge of curation; live replication; changing the schema for identity.
- Anti-goals: a sync that can silently overwrite or delete prod-origin data; a promote that leaves rows without their files (or
  the reverse, since `unregister_deleted_images` deletes rows whose files are gone); secrets or the main `DATABASE_URL` in scripts.

## Open questions (for the owner, in the S1 review)

See the "Open questions" section of the S1 spec.
