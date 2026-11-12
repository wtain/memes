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

## How the image files reach prod (nothing is copied by hand)

```
Who/when: you, on the dev workstation, by hand. `python -m sync.run --env general --apply` runs:
pull -> FILES (project P3) -> data (P2) -> retire -> verify. Or P3 alone:
`python -m sync.files promote --env general --apply`. Dry run is the default.

 dev workstation                      ssh/scp over the internet                      prod VPS (gate)
 1 read active, unflagged rows -> manifest {name, size, sha256}
 2 handshake + list  ----------------------------------------------------------->  names + sizes in BASE_PATH
 3 diff in memory: missing / changed / orphans (guards: 2 %, empty manifest)
 4 per batch (150-500 MB):
   pack tar -> upload -------------------------------------------------------->  STAGING/<id>.tar.part
   verify (sha256 of every file against the manifest) ------------------------>  extract, check, nothing visible yet
   commit -------------------------------------------------------------------->  rename(2) into BASE_PATH (appears whole)
 5 final listing check, write files-promoted.json (the report)
 6 P2 reads the report, then writes the database ROWS (only for verified files)
 7 later P2 deletes rows of retired images; then `files retire` moves their files to
   RETIRED/<date>/ (kept 14 days, never rm)
Rule: file first, row second when adding; row first, file last when removing.
A killed run is repaired by running it again. First seed of general: about 11 min at 50 Mbit/s.
```

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
| 4 | Statistics on prod | Serve a promoted snapshot (**reversed by decision 25**: the statistics page is dropped on prod) |
| 5 | Concepts / trends | **Concepts shipped (class D), trends hidden** (class X, `trends_batch` stays on dev) |
| 6 | Exposure | **Public internet**, no app login. Consequences: the history route and uploads are disabled on prod (below), reader write routes need rate limits, proxy logs must drop query strings |
| 7 | Postgres layout | One cluster, three databases on the VPS (**superseded by decision 17**: one stack and one Postgres per environment) |
| 8 | Index rebuild | **Owner step run locally on the VPS** for drop and rebuild; the promote tool (`sync_writer`) only inserts into existing indexes and may `REINDEX` |
| 9 | Uploads on prod | **`POST /api/uploads` disabled on prod**; phone and web upload to dev. No prod inbox, so P4 pulls no inbox files |
| 10 | `GET /api/history` | **Disabled on prod**; prod still records history and impressions for the pull |
| 11 | Retention on prod | **Keep everything**; prod is the system of record for reader data, no pruning |
| 12 | Android | **Add `GET /api/diagnostics/capabilities`**; web and Android read it to hide blocked screens (one app release) |
| 13 | Bug reports | **Kept on prod, size- and rate-limited**; dev pulls the files and prod copies are deleted only after a verified pull |
| 14 | Comics on prod | Built comics only, with a shipped built image (**reversed by decision 22**: comics tables are not shipped) |
| 15 | Runtime on the VPS | **Docker Compose**: one backend container per environment from `Dockerfile.backend`, one Postgres cluster in a container, a one-shot migration container (P5 design) |
| 16 | Reverse proxy | **Caddy** (TLS, hostname routing, access logs without query strings; P5 design) |
| 17 | Production shape | **One stack per environment** (review of P5, option A): one parameterised compose file run as three compose projects (`it`, `metal`, `general`), each with its own Postgres, backend and migration job; only Caddy is shared. Per-environment role names, passwords, ports and ssh keys. VPS of 4 GB or more. Supersedes the single shared cluster of decision 7 |
| 18 | Sync connection configuration | Secrets in a gitignored **`environments/.env.<env>.sync`** (restricted permissions); ambient `PROD_*` variables are ignored. The tool opens and closes the `ssh -L` tunnel itself from an ssh host alias (Windows OpenSSH, BatchMode, ExitOnForwardFailure). Every tool starts with an **identity handshake**: database name, role, a database-level `memes.environment` setting and the capabilities `environment` must all equal `--env`, or it exits. Supersedes the env-var URLs and the hand-opened tunnel |
| 19 | File transfer | **Bulk tar batches kept**, incremental by comparing prod with dev; SFTP would also be acceptable. Hardened: the gate extracts with a strict validator (regular files only, no links or special files, caps), the upload channel is a **separate upload-only account chrooted to staging**, the images folder is **read-only in the backend container**, and the backend refuses image paths outside it |
| 20 | Changed bytes | The promote **always sends dev's current bytes** (dev is the source of truth, files are fixed before they become active). No refuse rule and no extra flag; replaced files are a warning count in the report. A `?v=<hash>` cache-bust stays in the backlog |
| 21 | Credentials on the workstation | `.env.<env>.sync`, gitignored, restricted permissions; never loaded in agent sessions |
| 22 | Comics on prod | **Not shipped**: `comics` and `comics_panels` become class X; a built comics appears on prod as an ordinary image; comics routes are dev-only. **Reverses decision 14** |
| 23 | Abuse limits on the public prod | **Rate limits and size caps** (Caddy plugin or app level), caps on `q`/`facets` and on the bug-report body before it is read, and an alarm in the verify report for abnormal growth of the reader tables. No automatic pruning; decision 11 stands |
| 24 | Ratings routes on prod | **Disabled while /reply is off** (they return with meme-from-text). The ratings table stays class P and the pull stays in the design, moving nothing today |
| 25 | Statistics on prod | **Dropped**: route and page hidden, `statistics_snapshots` becomes class X. **Reverses decision 4** |
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
