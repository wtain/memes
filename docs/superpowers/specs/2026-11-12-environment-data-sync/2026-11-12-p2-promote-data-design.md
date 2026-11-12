# P2: promote data, dev to prod (design)

Status: draft
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (registry, built), S3 (`docs/adr/adr-2026-11-12-dev-prod-sync-technique.md`), S4 (`docs/adr/adr-2026-11-12-dev-prod-file-sync.md`),
S5 (`2026-11-12-s5-prod-topology-and-roles.md`), decisions log in `README.md`.
Pairs with: P3 (files) and P4 (pull). P2 never copies files; it needs P3's contract (below).

## Intent

One command, run on the dev workstation, that brings the class D data of the promotable images (and the D global sets) to a prod
database so that prod serves exactly what dev has approved, without ever touching prod-origin data and without leaving prod in a
half-promoted state. It is dry-run by default, resumable by construction, and refuses anything surprising.

## Goals / non-goals / anti-goals

- Goals: preconditions that stop a mismatched promote before any write; a plan (what is new, changed, retired, per table, with
  bytes) computed from per-image digests; an apply that is atomic per batch of images, safe to interrupt and re-run; a verification
  that proves prod now equals dev for class D; a first-seed path for an empty prod.
- Non-goals: file transfer (P3), the pull of reader activity (P4), prod provisioning, roles SQL, index drop-and-rebuild on prod
  (an owner step, decision 8), the capabilities endpoint (P5), any schema change or migration.
- Anti-goals: a promote that can write a class P or X table; one that deletes rows because dev looked empty; one that leaves an image
  visible without its file or without all its derived rows; one that logs row content; credentials in the repo or in shell history.

## Command

`python -m sync.promote --env {metal,general,it} [options]` (new modules in the `sync/` package; only `model.py`, `registry.py`
and `render.py` must stay standard-library only, P1 test). It uses psycopg2, already in the batch venv, and runs only on dev.

| Option | Meaning |
|---|---|
| (none) | Dry run: preconditions, plan, report, exit. Writes nothing on either side |
| `--apply` | Perform the promote. Asks to type the environment name unless `--yes` |
| `--first-seed` | Prod is empty and not serving: load tables with the pre-load index state left to the owner (below). Refused if prod has any image |
| `--only TABLE[,..]` | Restrict to class D tables (names are checked against the registry); parent tables are added when needed |
| `--limit-images N` | Cap new/changed images per run (for trial runs and resumption in pieces) |
| `--retire` | Include the retire phase (needs a recent pull, see Retire) |
| `--allow-mass-retire` | Lift the retire guard |
| `--skip-files-check` | Skip the "files present on prod" gate (dry runs and tests only, refused with `--apply`) |
| `--batch-size N` | Images per prod transaction (default 500) |

Exit codes: 0 clean (or dry run), 2 precondition failed, 3 guard tripped, 4 verification found differences, 1 other.

## Connections and credentials

- **Dev:** the normal repo path (`config.settings.load_env(args.env)`; `DATABASE_URL` from the environment). The promote only reads
  dev; it opens the connection read-only (`SET default_transaction_read_only`) and, if `DATABASE_URL_READONLY` is present, prefers it.
- **Prod:** `PROD_SYNC_DATABASE_URL` in the shell environment (an `ssh -L` local port, role `sync_writer`, per S5). Never in the repo, never on the
  command line. The tunnel is opened by the owner (a documented one-liner in the P7 runbook); the tool does not spawn ssh in v1.
- **Role check:** on connecting, `select current_user, rolsuper` and the table privileges of the role. Refuse if the role is the backend role, the
  owner role, or a superuser; refuse if it holds `UPDATE`/`INSERT` on any class P table (it must not be able to write reader data).
- A second prod connection is opened per worker for digests (read only, same role).

## Phases

### 0. Preconditions (read-only, both sides; fail closed)

From the S3 ADR, Q6: equal `alembic_version`; equal `server_version_num` major; equal `vector` and `pg_trgm` versions; equal vector dimensions (`atttypmod`)
for the shipped vector columns; exactly one `(model_name, model_digest)` pair in the promotable `meme_profile_situation_embeddings` and, if prod already
has rows, the same pair; one `taxonomy_version`; the connected prod role is the sync role; the advisory lock key is free; prod is not mid-promote (a
`pg_stat_activity` look for the sync application name). **Code and settings revision:** the CLIP, SBERT and note vectors carry no model marker, so parity
can only be checked as a revision: the tool reads the git SHA of the working tree and, if `PROD_API_URL` is set, `GET /api/diagnostics/capabilities` (P5);
a mismatch is a precondition failure. Until P5 exists the check is a printed warning that `--apply` requires `--ack-revision` for.

### 1. Plan

1. **Promotable set on dev:** ids from `PROMOTABLE_IMAGES_SQL` (P1).
2. **Digest manifests.** Per image, one hash over its rows in each digest group (`core, ocr, clip, tags, desc, profile, note`, and `comics` below),
   computed by one SQL per side: per table `count(*)` and `bit_xor(hashtextextended(<row content>, 0))` grouped by image, combined per group. Row
   content is every column of the table **except** an explicit exclusion list in the registry (`digest_exclude`: surrogate ids nothing references,
   `created_at`; `images.ingestion_batch_id`; `popularity` is **included**). Excluding is explicit, so a new column is in the digest by default.
   Vectors are hashed as text. Only `(id, group hashes)` crosses the tunnel (1.7 MB for general).
   **Registry amendment (T1):** add `digest_exclude` to `TableSync`, a test that excluded columns exist, and that the stale flags and
   `image_description_feedback.approved` are not excluded.
3. **Sets:** `new` (on dev, not on prod), `changed` (digest differs in any group), `unchanged`, `retire` (on prod, not promotable on dev).
   An image whose group set changed, for example it gained a description, is `changed`; it is replaced whole (all its D rows).
4. **Global sets** (`concepts*`, `statistics_snapshots`, `popularity_absorptions`, `comics*`): compared by key and row hash; planned as insert, update, prune
   (for tables with `prune`). Absorptions are append-only: planned as inserts of ids missing on prod, plus a check that no id on prod is absent on dev (a
   difference is reported, never deleted).
5. **Guards:** retire count above `max(50, 2 % of prod images)`; a plan that would leave prod with zero images; a promotable set smaller than half of prod's;
   `new + changed` above 25 % of a table (advice to use the owner's rebuild path, not a refusal); a dev with no promotable images. Any guard stops the run
   (exit 3) before a single write, except the advisory.
6. **Report:** counts per table, estimated bytes, batch count, the guard decisions, the list of problems (images whose file is missing on dev, ids of
   non-promotable images referenced by a shipped row). `--json` writes the plan for P6.

### 2. Apply

1. `pg_advisory_lock(<fixed key>)` on prod, held for the whole run; `SET application_name`, `SET maintenance_work_mem = '1GB'` (and
   `lock_timeout`, `statement_timeout` sane defaults), session `synchronous_commit` unchanged.
2. **Files gate (P3 contract).** For the batch, every image's file must already be on prod with the manifest's size and `content_hash` (P3 copies and verifies
   it first). Prod's files are not visible through Postgres, so the gate is the P3 report file (`files-promoted.json`, per run id) that lists the verified
   images; an image not in it is skipped and reported, not committed (Q5 of the S3 ADR). Until P3 exists, `--apply` refuses without
   `--skip-files-check`, so a P2-only deployment can only be tried on a database whose files are known to be in place.
3. **Batches of images** (new and changed, ordered by `created_at, id` for determinism): one prod transaction per batch:
   - `changed`: the image keeps its `images` row; its derived rows are deleted by image in reverse level order (children first) and re-inserted.
     `images` and `image_extras` are upserted.
   - copy the rows from dev with `COPY (SELECT ... WHERE image_id = ANY(%s)) TO STDOUT (FORMAT binary)` streamed into `COPY ... FROM STDIN (FORMAT binary)`
     in registry load order; `nullify` columns are written as NULL (`images.ingestion_batch_id`, `comics_panels.image_id` when the source image is not shipped).
   - commit. A failed batch rolls back and stops the run (exit 1); finished batches are final and the next run picks up the rest from the digests.
4. **Global sets**, each in its own transaction in registry level order (concepts, then absorptions, statistics, comics). Rules:
   - `concepts*`: replaced as a set (integer ids, one origin): delete missing, upsert the rest by id, in one transaction, then `setval`.
   - `popularity_absorptions`: insert missing ids in `created_at` order (absorb row, then its reverse row, then `UPDATE` the absorb row's `reversed_by_id`); the open-loser unique index and the shape check
     forbid a NULL-first load. The table has no FK to images, so it does not have to wait for the images it mentions.
   - `statistics_snapshots`: upsert the `corpus` row.
   - `comics`, `comics_panels`: see Comics.
5. **Retire** (only with `--retire`): see below.
6. **After the data:** `setval(max(id))` for the registry's `setval` D tables; `ANALYZE` of every touched table; `VACUUM (ANALYZE)` of the HNSW tables
   after a retire. The unlock is in a `finally`.

### 3. Verify

Recompute the digest manifest on both sides for the promoted images and the global sets; every table's row count over the promotable set equal on both
sides; report differences (exit 4) with ids only. Run unconditionally after `--apply`, and as the `--verify-only` mode used by P6.

## Comics (decision: built only, with a shipped built image)

A comics ships when `state = 'built'` and its `built_image_id` is in the promotable set. Its panels ship with it. Panel `image_id` is blanked when that
source image is not shipped (the common case, unkept panels are flagged); the snapshot columns stay. Consequences, to implement in T1:

- **Registry amendment:** `comics.scope = image_fk("built_image_id")` with an additional filter `state = 'built'` (new `Scope.where` field), no `nullify` for
  `built_image_id`; `comics_panels` stays `via(comics)`, `nullify = image_id`. `prune` stays on both: a comics unbuilt or unpromoted on dev leaves prod.
  Both join the digest group `comics`, keyed by the built image, so a changed panel set shows up as a changed image. The P1 test that requires image-scoped children
  to be replaced whole gets `comics` on its allowlist (it is replaced as `comics` row plus panels per built image).
- **Unique keys.** `comics.name_key`, `comics.built_image_id` and `comics_panels (comics_id, position)` / `image_id` are unique. A rename-swap or a moved panel could
  violate them transiently in an upsert. The promote therefore replaces a changed comics whole, in the order: delete its panels, delete the comics row, insert the comics,
  insert its panels, inside the image's batch transaction; and processes comics deletions before insertions.

## Retire

Image ids on prod that are not in dev's promotable set (flagged, cut, rejected, deleted on dev).

1. **Precondition:** a P4 pull has completed for this environment within the last N hours (`pull_state` file written by P4 next to the plan outputs;
   N default 24), because `DELETE FROM images` cascades into `meme_text_ratings` (registry flag `pull_before_promote`). Until P4 exists, `--retire` requires
   `--i-have-pulled`.
2. Guard (above). Then batches of 500 ids: `DELETE FROM images WHERE id = ANY(%s)` in the batch transaction; the cascade removes every shipped table's rows.
   `comics` whose built image is deleted are deleted explicitly (the foreign key only sets NULL).
3. **P3 deletes the files of retired images after this commit** (rows first, files afterwards): the plan output lists the ids and filenames.
4. Reader rows (`meme_saves`, `meme_impressions`, `search_history`, `search_not_found`) are left alone, orphans by design.

## First seed and index state

For an empty, not-serving prod. The owner runs the documented prep on the VPS first (P5: drop the HNSW and GIN indexes with the owner role; the sync role cannot do
DDL, decision 8); the tool then detects missing indexes through the catalog and does a plain bulk load (single transaction per table, level order), refusing
if a serving marker is present or any image exists. After the load it reports that the indexes must be rebuilt by the owner step and runs nothing else.
Incremental promotes on a serving prod keep the indexes in place (S3 Q2) and use the batch path above; a plan that touches more than about 25 % of a table prints the
recommendation to switch to the owner's rebuild window.

## Failure and resume

- Interrupted between batches (Ctrl-C, network drop, tunnel loss): committed batches stay; re-running computes the same digests and continues. Ctrl-C during a batch
  rolls it back (`KeyboardInterrupt` handled; the COPY streams are cancelled).
- A lost advisory lock connection ends the run with an error; Postgres releases the lock with the session.
- No prod write happens outside a transaction that also contains the `images` row of the affected images, except `setval`, `ANALYZE` and `VACUUM`.
- Idempotent: a second run right after a clean one plans nothing.
- Log lines carry table names, counts and ids only; never row values, filenames only in the `--json` plan, never connection strings.

## Tests

1. **Pure unit tests** (no database): plan computation from two manifests (new, changed, retire, unchanged, guard trips, the 25 % advice), digest-column selection from
   the registry, batch splitting, role-check logic against fake privilege rows, exit codes, `--only` parent expansion, refusal rules of the options.
2. **Integration** (real Postgres, two databases on the test server: a "dev" and a "prod" with the real schema applied through alembic; `tests/integration/sync/`,
   needs `DATABASE_URL` like the other integration tests, see the test-database note below). Scenarios: first seed; idempotent second run; new image; every kind of
   change (re-embed, new description, in-place tag edit, remark, popularity, feedback flip, stale flip) is detected and replaced whole; a removed tag disappears on prod; retire with
   the cascade and the pull precondition; ratings untouched when the image stays; comics (build, rename-swap, panel move, unbuild, image unpromoted); absorptions with a
   reversed pair; concepts `setval` then a default insert; statistics upsert; nullified columns; a promote interrupted by an injected failure in batch 2 and then resumed;
   mass-retire guard; attempt to write a class P or X table (must be impossible: the sync role has no privilege, and the code never names one: a test walks the executed SQL).
3. **Registry amendment tests** (T1): comics scope, digest_exclude coverage, the P1 mutation list extended.
4. **Not covered by tests:** a real SSH tunnel, a real VPS, a real network drop (S3/S4 "not verified"); a rehearsal on a throw-away VPS or a second local Postgres with latency
   injected is the P7 acceptance step.

**Test database note (to verify in T0):** the integration suite uses one database (`ocrdb_test`). Two databases need either `CREATEDB` for the `ocr` role on the test
server, or two schemas selected by `search_path` on one database. T0 checks which works locally and in the CI service container; the second option needs the tool to accept
a schema (`options=-csearch_path=...`), which is easy and also useful for rehearsals.

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T0 | Spike: two databases or two schemas for tests locally and in CI; the fixture | none |
| T1 | Registry amendment: `digest_exclude`, `Scope.where`, comics scope, tests, page regenerated | P1 |
| T2 | `sync/promote/` skeleton, options, exit codes, connections, role check | T0 |
| T3 | Preconditions (phase 0) with unit tests | T2 |
| T4 | Digest SQL generator from the registry and the manifest diff, plan and report, guards | T1, T2 |
| T5 | Apply: advisory lock, batches (copy, nullify), per-image replace, `setval`, analyze | T4 |
| T6 | Global sets: concepts, statistics, absorptions order, comics replace | T5 |
| T7 | Verify phase and `--verify-only` | T5 |
| T8 | Retire phase with the pull precondition and the guard | T5 (P4 for the real precondition) |
| T9 | First-seed mode | T5 |
| T10 | Integration scenarios, failure injection | T5 to T9 |
| T11 | Docs: `docs/batches/` or a new `docs/sync/promote.md`, `backend_api.md` unaffected, CLAUDE.md pointer | all |

Size: large. Mode: subagent-driven, one task per fresh subagent in dependency order (T0, T1, T2 and T3 can overlap; T5 to T9 sequential because they share the
apply loop). Controller re-runs the tests after every task. Reviews, two fresh subagents after T10: **data safety** (can any path write prod outside the rules, delete wrongly,
half-promote, leak data in logs) and **correctness vs the S3 ADR and the registry** (digest coverage, order, comics rules, resume).
Live databases: none are touched during development; the first run against a real prod is the P7 rehearsal, with the owner present.

## Choices made by me (to confirm)

- The module layout (`sync/promote/*`), the option names, and exit codes.
- `PROD_SYNC_DATABASE_URL` as the only way to name prod; the tool does not manage the ssh tunnel in v1.
- Digest by default over all columns with an explicit exclusion list (stronger than the S3 plan: new columns cannot be silently skipped).
- Batch size 500, retire guard `max(50, 2 %)`, pull freshness 24 h, 25 % advice threshold (all adjustable).
- The "files present" gate reads a P3 report file, not the filesystem of prod.
- Absorptions on prod that are absent on dev are reported, never deleted (append-only audit data).
- Typing the environment name to confirm `--apply`.

## Risks and what is not settled

- The 25 % crossover and HNSW timings were measured on synthetic data; the real first seed of general is the actual test (P7).
- Model parity of CLIP and SBERT vectors is a revision check, not a data check.
- The P3 files gate and the P4 pull freshness file are interfaces that do not exist yet; their formats are fixed in the P3 and P4 designs and T5/T8 adapt.
- `popularity_absorptions` shipped as a whole set grows forever; fine at today's size (1 row on general), revisit if it grows.

## Open for the owner

None blocking. The choices above are for review.
