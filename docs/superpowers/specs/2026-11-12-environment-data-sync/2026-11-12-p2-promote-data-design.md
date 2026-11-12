# P2: promote data, dev to prod (design)

Status: draft (revised after design review 2026-11-12; reconciled with `interfaces.md` 2026-11-12; revised again for decisions 26 (dry-run report) and 27 (no removals for now) on 2026-11-12)
Interfaces: every shared name (state files, settings, roles, ports, handshake steps H1 to H10, flags, exit codes) is defined in `interfaces.md`; this document does not redefine them.
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Review: docs/superpowers/reviews/2026-11-12-p2-promote-data-design-review.md (disposition at the end of this document)
Depends on: P1 (registry, built), S3 (`docs/adr/adr-2026-11-12-dev-prod-sync-technique.md`), S4 (`docs/adr/adr-2026-11-12-dev-prod-file-sync.md`),
S5 (`2026-11-12-s5-prod-topology-and-roles.md`), decisions log in `README.md` (decisions 1 to 27; 17 to 27 override older text).
Pairs with: P3 (files) and P4 (pull). P2 never copies files; it consumes P3's `files-promoted.json` and produces `committed.json` and its part of the dry-run report (`interfaces.md` k). It does not read `pull_state.json` and removes nothing from prod (decision 27). P2 also builds the shared connection modules and the report module (work item SC, tasks T2 and T3).

## Intent

One command, run on the dev workstation, that brings the class D data of the promotable images (and the global D sets: concepts and absorptions) to the
prod database of the same environment, so that prod serves what dev approved, without touching prod-origin data and without leaving prod half-promoted.
Dry run by default, and the dry run is a **report** of what would be added to prod and what would be overwritten there (decision 26, `interfaces.md` k); the owner reads it before every apply. Resumable by construction, and it refuses anything surprising: the wrong prod, a dev that is mid-rebuild, a dev that lost data.
**Nothing is removed from prod, for now (decision 27):** the promote adds new images and overwrites changed ones; what dev no longer promotes stays on prod and is only reported as left behind.

## Goals / non-goals / anti-goals

- Goals: an identity handshake before any read or write; a plan from per-image digests; a report of the plan (added, overwritten, unchanged, skipped, left behind) that the apply reproduces; an apply that is atomic per batch of images and safe to re-run;
  a verification against what was promised; a first-seed path; result files that P3 and P6 can trust.
- Non-goals: file transfer (P3), the pull (P4), provisioning and roles SQL (P5), index drop and rebuild on prod (owner step), any schema change, any removal from prod (deferred, decision 27).
- Anti-goals: a write to a class P or X table; any delete of an image or of a global-set member on prod (nothing is removed for now; only the derived rows of a changed image are replaced); an overwrite because dev looked empty or half-built; an image visible without its file or its rows; row content in any
  log or exception text; credentials in the repo, in shell history, in the process environment or on a command line.

## Command

`python -m sync.promote --env {metal,general,it} [options]`; the usual entry is `python -m sync.run --env E [--apply]` (P7) which runs handshake, pull, files, promote, verify under one run id (the retire steps are deferred, see the last section). Package `sync/promote/`; `sync/model.py`, `registry.py`, `render.py` stay standard library only.
psycopg2 is used; the tool runs only on dev. There is no connection option: the target is derived from `--env` (Connections). Flags are defined once in `interfaces.md` (section i); this table lists what promote accepts.

| Option | Meaning |
|---|---|
| (none) | Dry run: handshake, preconditions, plan, **report** (`interfaces.md` k: `report.json`, `report.md`, stdout summary, plan hash, the exact apply command), exit 0. Writes nothing on either side and no state P3 consumes |
| `--apply` | Perform the promote. Typed confirmation `<env> <prod database name>` (the name read back from the server), unless `--yes` |
| `--plan-hash H` | With `--apply`: re-plan and refuse (exit 3) if the plan hash differs from the dry run's printed hash (the apply reproduces the plan the owner read) |
| `--first-seed` | Prod database is in state `seeding` (below); loads by image batches, idempotent and resumable |
| `--only TABLE[,..]` | Restrict to class D tables (validated against the registry); parents are added |
| `--limit-images N` | Cap new and changed images per run. With it, the global sets are skipped (the report lists the capped images under `skipped_with_reason: capped_by_limit`) |
| `--allow-mass-change N` | Lift the mass-change guard; N must equal the planned `changed` count exactly |
| `--allow-shrink TABLE` | Lift the regression guard for one table (repeatable) |
| `--ignore-running-batches` | Lift the running-batch precondition |
| `--batch-size N` | Images per prod transaction (default 500) |
| `--verify-only` | Phase 3 only, against `committed.json` of the newest run (used by P6) |
| `--max-report-age D` | Age limit of `files-promoted.json`, default **2 h** |
| `--top N` | Ids per category in `report.md` and the stdout summary (default 50) |
| `--run-id ID`, `--json PATH` | Run id passed by `sync.run`; copy of `report.json` (counts and ids only) |

Exit codes as in `interfaces.md` (0 clean or dry run; 1 other, a partially applied run lists the committed batches; 2 handshake or precondition; 3 guard before any write; 4 verification differences).
There is no `--skip-files-check`, no `--ack-revision`, no `--i-have-pulled`, no `--allow-replace`, no `--prod-url`.

## Connections, credentials, identity handshake (decisions 18, 21)

**Where things come from.** Secrets only from `environments/.env.<env>.sync` (gitignored; keys `SYNC_WRITER_PASSWORD`, `SYNC_READER_PASSWORD`, nothing else), read with `dotenv_values` into a dict
(never into `os.environ`); the loader refuses a file that git tracks and warns if other Windows accounts can read it. No `PROD_*` variable is read; an ambient one is ignored with a warning naming it.
Everything else is tracked, non-secret, per environment in `settings.<env>.yaml` under `sync.*` (full list and values in `interfaces.md`, sections b and d):
`sync.prod.{database, writer_role, reader_role, ssh_alias, local_port, pg_host, pg_port, api_url}`, `sync.dev.database`, `sync.ssh_bin`, `sync.state_dir`. The expected environment is `--env`
itself. This split is the pin: where to connect (secret file) and what must be found (tracked) come from different places. **P2 builds the shared modules** `sync/config.py`, `sync/tunnel.py`
and `sync/connect.py` (handshake, role check, connection factories, `sync.doctor`) as work item SC (tasks T2 and T3); P3, P4, P6 and P7 only consume them.

**Tunnel.** `sync/tunnel.py` opens and closes `ssh -N -T -L 127.0.0.1:<local_port>:<pg_host>:<pg_port> <ssh_alias>` with the Windows OpenSSH binary by full path, `BatchMode=yes`,
`ExitOnForwardFailure=yes`, `StrictHostKeyChecking=yes`, keepalives, `127.0.0.1` never `localhost`; torn down in `finally` and `atexit`; ssh stderr goes to a temp file, never to the log.

**Handshake, steps H1 to H10 of `interfaces.md` (section e), in this order; any failure is exit 2 with a one-line reason, before the first read of dev data or write of prod.** Promote runs H1 to H5 and H7 to H10 (no gate step); the details specific to promote:

| # | Check | Expected value from |
|---|---|---|
| H1 | Dev side is the right dev: `APP_ENV` equals `--env`; the dev connection uses `DATABASE_URL_READONLY` (the write-capable URL is refused); dev `current_database()` equals the tracked `sync.dev.database`; dev has no `memes.environment` setting | tracked settings |
| H2 | Prod `current_database()` equals `sync.prod.database` | tracked settings |
| H3 | Prod `current_user` equals `sync.prod.writer_role` (reader role for read-only tools); not superuser, not owner, not the `backend_app` role (`pg_has_role`), no `rolcreaterole/createdb/replication/bypassrls`; `has_table_privilege` for INSERT, UPDATE, DELETE, TRUNCATE is false on every class P and X table (true on the class D tables the writer must write); the single column-level `SELECT (image_id, query_hash, updated_at)` on `meme_text_ratings` is expected and nothing wider (`interfaces.md` h) | registry |
| H4 | Prod database-level setting `current_setting('memes.environment')` equals `--env` (set by the owner role with `ALTER DATABASE ... SET memes.environment`, P5) | `--env` |
| H5 | Capabilities: `GET <api_url>/api/diagnostics/capabilities` returns `environment` equal to `--env` and `stage = prod`. Nothing else is read from it (no revision, no schema head) | `--env` |
| H7 | Not the same server: dev and prod must differ in at least one of `(inet_server_addr, port)`, `pg_postmaster_start_time()`, database oid, and `pg_control_system().system_identifier` where readable | both |
| H8 | Id overlap, not liftable: if prod has images, at least half of prod's image ids exist in dev's `images` (any status); a pairing with the wrong environment has overlap 0 | both |
| H9 | Prod state: `current_setting('memes.state')` is `serving` for a normal promote, `seeding` for `--first-seed` (database setting set by `provision.sh`, P5; flipped to `serving` by the owner after the index rebuild) | mode |
| H10 | Release: `alembic_version` equal on both sides and `current_setting('memes.revision')` equals the dev working tree's git SHA (a mismatch is a precondition failure; this replaces `--ack-revision`) | both |

A second connection per worker for digests uses the same role. The banner is printed on every run and repeated at the confirmation:
`dev <env> (<dev db> @ localhost, read-only) -> prod memes_<env> as memes_<env>_sync_w via ssh alias memes-prod-<env>`. `python -m sync.doctor --env E` runs the tunnel and the handshake and writes nothing.

## Phases

### 0. Preconditions (read-only; fail closed; exit 2 unless stated)

| Precondition | Detail |
|---|---|
| Schema parity | Equal `alembic_version` and `memes.revision` equal to dev's git SHA (H10); equal `server_version_num` major; equal `vector` and `pg_trgm` versions; `server_encoding` UTF8 on both; equal vector dimensions (`atttypmod`) |
| Column parity (H6) | For every shipped table the list `(column name, data type, nullability)` from `information_schema.columns` is identical on both sides **and** equals the columns of `Storage.models`. Catches a hand-edited dev database that the alembic check cannot |
| Model parity | Exactly one `(model_name, model_digest)` in the promotable `meme_profile_situation_embeddings` and, if prod has rows, the same pair; one `taxonomy_version`. CLIP, SBERT and note vectors carry no marker: parity is the revision check (handshake 5) |
| No dev batch is running (C1) | Dev `batch_runs` has no row with `status = 'started'`, except kind `ingestion` (the long-lived review row; its writers touch pending images only). Lifted by `--ignore-running-batches`, which is printed in the report |
| Locks | The state-directory `lock` file on dev (shared by all sync tools, `interfaces.md` a), and `pg_try_advisory_lock(<fixed key>)` on prod (keys are per database, so environments do not collide); busy means exit 2 "another promote holds the lock". `pg_stat_activity` look filtered by `datname` for the sync application name |

### 1. Plan

1. **Promotable set** on dev: ids from `PROMOTABLE_IMAGES_SQL` (P1). Dev is read in a snapshot (below).
2. **Digest manifests (H6, M3).** Per image and per digest group (`core, ocr, clip, tags, desc, profile, note`; there is no comics group, decision 22), one SQL per side per table.
   The digest is independent of session formatting, column order and locale by construction:
   - Every digest session runs `SET TimeZone='UTC'; SET DateStyle='ISO, YMD'; SET extra_float_digits=1; SET bytea_output='hex'; SET IntervalStyle='iso_8601'; SET client_encoding='UTF8'` and reads each back with `SHOW`; a mismatch is exit 2.
   - Row content is `ROW(c1, c2, ...)::text` over an **explicit column list**: the model's columns for that table in **alphabetical order**, minus the registry's `digest_exclude`. Never `t::text`, never `*`, never `concat_ws` (a NULL differs from an empty string in `ROW(...)::text`). `vector` and `json` columns are cast to text; `jsonb` is normalised, which is acceptable.
   - Per table and image: `count(*)`, `sum(hashtextextended(row_text, 0)::numeric)` and `sum(hashtextextended(row_text, 1)::numeric)`. Sums, not `bit_xor`: identical rows no longer cancel. The group hash is `md5` of the per-table triples in registry order. Only `(id, group hashes)` crosses the tunnel.
   - **`digest_exclude` (registry amendment, task T1):** a per-table tuple of column names, each with a stated reason: unreferenced surrogate ids and `created_at` of `ocr_texts`, `embeddings`, `tags` (a rebuild with the same content is not a change; sums count duplicates correctly); `images.ingestion_batch_id`; `description_notes.lemmas_built_at` and `embedding_built_at` (pure bookkeeping, M4). Everything else is in the digest by default, including `popularity`, every stale flag, `image_description_feedback.approved` and `meme_profiles.updated_at` (check before excluding). A test asserts the excluded columns exist and that stale flags, `approved` and `popularity` are not excluded.
   - **Registry amendments (also T1):** `image_extras` becomes `REPLACE_PER_IMAGE` (M1; reason in `interfaces.md`, resolved call 4); `images` remains the only upsert among image-scoped tables; the
     `popularity_absorptions` note says "insert-or-close"; the P1 mutation test follows. P1's text lists these three as its follow-ups.
3. **Sets:** `new` (reported as `added`), `changed` (digest differs in any group; reported as `overwritten`, with the changed groups per image), `unchanged`, and `left_behind` (on prod, not promotable on dev: informational only, nothing is done about it, decision 27). A changed image is **overwritten**, not removed: the image stays and all its D rows are deleted children first and re-inserted (this is also how a removed tag, a cleared note or a removed remark leaves prod, M1; it is an overwrite of the image's derived rows, in scope).
4. **Completeness floor (M8):** a `new` image without an `embeddings` row or without an `ocr_texts` row on dev is skipped and listed, not shipped (`sync.promote.required_tables`, tracked). A `changed` image is not held back by it.
5. **Global sets:** `concepts*` compared by key and row hash (insert and update only: **upsert, no prune** for now; the registry's `prune` field stays in code and P2 ignores it until removals are built, decision 27; members that prod has and dev lacks are `left_behind`). Absorptions: see Apply step 4.
6. **Guards (exit 3, before any write).** Each names the table or count and the flag that lifts it, if any.

   | Guard | Rule | Lift |
   |---|---|---|
   | Dev emptied | No promotable images; or the promotable set is smaller than half of prod's (a dev that lost data must not overwrite prod) | none |
   | Mass change (C1) | `changed` above 25 % of prod's images on a serving prod | `--allow-mass-change N` |
   | Row-count regression (C1) | For every shipped table, over the `changed` plus `unchanged` promotable images: dev's row total lower than prod's for the same ids by more than `max(100 rows, 2 %)` | `--allow-shrink TABLE` |
   | Coverage regression (C1) | The share of promotable images with at least one row in a table (`ocr_lemmas`, `embeddings`, `tags`, `image_descriptions`, `meme_profiles`) lower on dev than on prod by more than 1 point of images; or more than 1 % of images lose all rows of a table they have on prod | `--allow-shrink TABLE` |
   | Advice only | `new + changed` above 25 % of a table: print the owner's index-rebuild recommendation | n/a |

7. **Report (decision 26, format in `interfaces.md` k, built with `sync/report.py`).** The promote section of `<state dir>/runs/<runid>/report.json` and `report.md` (and the stdout summary) holds, per table and over the images:
   - `added`: new images and their rows (counts per table, bytes, the complete id list); new global-set members (concept rows, absorptions);
   - `overwritten`: changed images, each with the digest groups that changed (`core, ocr, clip, desc, tags, profile, note`) and a count per group, the rows replaced per table and their bytes; changed global-set members (concept rows by key, absorptions closed);
   - `unchanged`: counts per table;
   - `skipped_with_reason`: new images held back by the completeness floor (`missing_required_table`), images whose dev file is missing or whose name is a problem (as P3's `left_out`), images the files gate would skip (`files_gate_miss`, `waiting_for_files` in a `sync.run` dry run), ids deferred because dev changed since the plan (`deferred_changed_since_plan`), non-promotable ids referenced by a shipped row (`non_promotable_reference`), images over `--limit-images`;
   - `left_behind`: image ids on prod that are not promotable on dev, concept members and absorptions only prod has (informational, "left behind on prod (removals deferred)", never a guard);
   - the guards (each with value, limit, tripped or not, and the flag that lifts it), the preconditions and handshake results, the batches, the estimated duration and bytes on the wire, digest wall time, the plan hash and the exact `--apply --plan-hash` command.
   `report.json` holds the complete id lists, `report.md` the first N (default 50) per category. The report holds ids and counts only, never row values. A dry run writes no `committed.json`.
   **Reproducibility:** `--apply` recomputes the plan with the same code and refuses with exit 3 if its hash differs from `--plan-hash`; the apply's own `report.json` carries a `result` block (committed counts per category) that must equal the plan except for the entries it lists as skipped or deferred.

### 2. Apply

1. Recompute the plan; with `--plan-hash` refuse (exit 3) when the hash differs. Lock held for the whole run (`finally` unlocks); `SET application_name`, `maintenance_work_mem`, `lock_timeout`, `statement_timeout`. Digest workers default to 2.
2. **Files gate (H2, decisions 19, 20).** The input is P3's `files-promoted.json` (this run's when called by `sync.run`, else the newest non-dry one of the environment), not older than `--max-report-age` (default **2 h**), with `env` equal `--env`,
   and its `manifest_sha256` equal to `sync/files/manifest.py: manifest_sha256()` recomputed by P2 from the promotable rows at start (a `fix_image_formats` run between report and apply changes the hash and invalidates the report). Per image, immediately before commit, P2 compares the dev row it is about to copy
   (`filename`, `content_hash`; file size is not a column) with the report entry `(id, filename, content_hash)`; any difference or absence means the image is skipped, listed, not committed. Changed bytes are always sent
   by P3 and always promoted here (decision 20); there is no refuse rule. No file report, no apply.
3. **Image batches** (`new` and `changed`, ordered by `created_at, id`; additions and overwrites only). Per batch:
   - **Dev side (H5):** one transaction `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, per batch not per run. Inside it: recompute the batch's digests, re-check the promotable query for the ids (ids no longer promotable or changed since the plan are deferred and reported, picked up next run), and perform every `COPY ... TO` read. It is closed after the prod commit.
   - **Prod side, one transaction:** for `changed`, delete derived rows children first (registry delete order), including `image_extras`; upsert `images` with `execute_values` (`ON CONFLICT (id) DO UPDATE`, no staging table, no TEMP privilege); insert the rest by binary `COPY` with explicit alphabetical column lists in registry load order; `nullify` columns written NULL.
   - Commit. Then append the batch to `<state dir>/runs/<runid>/committed.json` (atomic): ids, the digests **of the snapshot that was copied** and `renamed` (`id`, `old_filename`, `new_filename` for every image whose prod filename changed, read from prod's row before the upsert; the old file stays on prod as a left-behind file until removals are built). A failed batch rolls back and stops the run (exit 1); finished batches are final and a re-run continues from the digests.
4. **Global sets**, each its own transaction, registry level order, after all batches, never with `--limit-images`:
   - `concepts*`: **upsert by id, nothing deleted** (decision 27; members only prod has are reported as left behind), then `setval(seq, coalesce(max(id), 1), max(id) IS NOT NULL)` (L3).
   - `popularity_absorptions` (H4): **insert-or-close**, not insert-only. Plan = rows missing on prod, plus rows present on both where dev's `reversed_by_id` is set and prod's is NULL; rows compared by hash with the `timestamptz` pinned to UTC. Per `loser_image_id` (rows with a NULL loser, `adjustment` rows, are independent), in one transaction and **not** by `created_at` (one dev transaction shares one timestamp):
     (a) close every absorb row that exists on prod open but is closed on dev (insert its reverse row, then `UPDATE ... SET reversed_by_id`); (b) then each new absorb that dev has closed, strictly one after the other as absorb with NULL, its reverse row, the update; (c) last the single open absorb of that loser (dev's own `uq_popularity_open_loser` allows at most one). A `reversed_by_id` pointing at a row missing from the fetched dev set is a hard error. Absorptions on prod that dev lacks are reported, never deleted.
   - No statistics and no comics section exist (decisions 22, 25).
5. **After the data:** `setval(max(id))` for registry `setval` D tables (coalesce form), `ANALYZE` of touched tables. (A retire phase and its `VACUUM` of HNSW tables are deferred, decision 27.)

### 3. Verify

Recompute digests on both sides for the images in `committed.json` and the global sets. Compare prod with **what was promised** (the recorded snapshot digests), not with dev now:
a difference is exit 4, ids only. Separately report, as information, `left_behind` (image ids on prod that dev no longer promotes, e.g. flagged on dev after promotion; counts and ids, never an error, decision 27), images that dev changed since the copy (they are next run's `changed`) and, for images whose
prod digest differs although dev did not change since the recorded promote, "prod drift" (L8). Row counts per table over the promoted set must match. Always runs after `--apply`; `--verify-only` re-runs it.

## Removals are deferred (decision 27)

Nothing is removed from prod and no removal is built for now. In this design that means: no retire phase (`--retire`, `retired.json`, mass-retire guard, ratings-cascade protections tied to it), no prune of global sets (upsert only), and orphans on prod are only reported (`left_behind`).
Consequences, stated so nobody is surprised: **an image flagged, cut, rejected or deleted on dev after it was promoted stays visible on prod (row and file) until removals are built**; an image whose filename changed on dev leaves its old file behind on prod; the ordering "pull before promote" and the ratings-cascade guard only matter once rows can be deleted, so the pull stays a step of `sync.run` but is **no longer a precondition of the promote**
and P2 does not read `pull_state.json`. The full design of the removal phase is kept in the last section, "Deferred phase: removals", for the day it is built.

## First seed and index state (H7)

An empty prod database is provisioned by the owner with the database setting `memes.state = 'seeding'` (P5 `provision.sh`; the only seeding marker), without HNSW and GIN indexes (drop-and-rebuild is an owner step; the sync role cannot do DDL, decision 8).
`--first-seed` requires that state, refuses a prod already `serving`, and is the **same image-batch path** (images with all their rows per transaction, plan from digests), so it is idempotent and resumable
after a crash: re-run `--first-seed`; no wipe is needed. The normal mode refuses `seeding`. Missing indexes are detected through the catalog; the report states that the owner must rebuild the indexes and then
set `memes.state = serving` as the last step. Incremental promotes on a serving prod keep indexes in place (S3 Q2).

## Failure and resume

- Interrupted between batches (Ctrl-C, tunnel loss, `ServerAliveCountMax` timeout): committed batches stay, the next run continues. Ctrl-C during a batch rolls it back.
- A lost lock connection ends the run; Postgres releases the lock with the session.
- No prod write happens outside a transaction that contains the `images` row of the affected images, except `setval`, `ANALYZE`, `VACUUM`, and the per-loser absorption transactions.
- A second run with dev frozen plans nothing (dev jobs move bookkeeping columns, which is why they are excluded).
- **No row content in logs or exceptions (M5).** Every database call is wrapped: only `sqlstate`, `diag.constraint_name`, `diag.table_name` are logged, never `str(exc)`, `diag.message_detail`, `diag.context` or the statement; an `excepthook` does the same; ssh stderr is filtered. A test forces a unique violation with a sentinel string and asserts it is absent from captured output. Prod's own server log keeps statement and DETAIL; noted as accepted for P5/P7.
- Log lines carry table names, counts and ids; filenames only in the state files; never connection data.

## Tests

1. **Pure unit tests:** plan from two manifests (new, changed, left behind, unchanged), every guard and its counted flag (a wrong N is refused), digest SQL generation (explicit alphabetical column lists, `digest_exclude`, pinned settings), batch splitting, role-check logic against fake privilege rows, handshake order and each failure, exit codes, `--only` expansion, option refusals, absorption ordering (a reversed pair then a re-absorb; two reversed plus one open of one loser; close of an already-shipped open row), no-leak test (M5). **Report checks (decision 26):** completeness (every category and every guard present, ids of every set in `report.json`, the first N in `report.md`, changed digest groups named per overwritten image, `waiting_for_files` shown in a combined dry run), reproducibility (two plans of frozen data give the same hash; a change between dry run and apply makes `--apply --plan-hash` exit 3; the apply's `result` block equals the plan), `left_behind` is informational (exit 0, never a guard), no content (sentinel text in a note, tag name or filename never appears in `report.json`, `report.md` or stdout; ids and counts only).
2. **Integration** (real Postgres, two databases or schemas, `tests/integration/sync/`, `DATABASE_URL` as for the other integration tests): first seed and its resume; idempotent second run with dev frozen; every kind of change detected and replaced whole (re-embed, new description, in-place tag edit, remark added and removed, note cleared, popularity, feedback flip, stale flip); digest identical under two different session time zones and different physical column order; a running batch row blocks; a half-rebuilt dev (a table emptied for 30 % of images) trips the regression guard; wrong-environment pairing (disjoint ids) refused whatever flags; an image flagged on dev after its promotion stays on prod untouched and is reported as left behind (exit 0), also a concept only prod has; dev modified mid-batch (snapshot isolation, verify compares with the recorded digests); files-gate mismatch on `content_hash` skips the image; concepts `setval` then a default insert; nullified columns; failure injected in batch 2 and resumed; no write to a class P or X table (a test walks the executed SQL; the role has no privilege) and no `DELETE FROM images` or delete of a global-set member in the executed SQL; a dry run followed by `--apply --plan-hash` on frozen dev applies exactly what the report listed.
3. **Registry amendment tests:** `digest_exclude` coverage, `image_extras` mode, mutation list extended.
4. **Not covered:** a real SSH tunnel, a real VPS, a real network drop (S3/S4 "not verified"); the P7 rehearsal covers them.

**Test database note (T0):** the integration suite uses one database (`ocrdb_test`); two databases need `CREATEDB` for `ocr`, or two schemas selected by `options=-csearch_path=...`; T0 decides.

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T0 | Spike: two databases or schemas for tests locally and in CI (test fixture) | none |
| T1 | Registry amendment: `digest_exclude`, `image_extras` mode, absorptions note, tests, page regenerated | P1 |
| T2 | **SC part 1:** `sync/config.py` (settings, `.env.<env>.sync` loader, state dir, run id, lock), `sync/tunnel.py` with fake-ssh tests, and `sync/report.py` (report model, plan hash, json/md/stdout writers, redaction guard; `interfaces.md` k) | T0 |
| T3 | **SC part 2:** `sync/connect.py` (handshake H1 to H10, role check, connection factories, capabilities client with a mock until P5a-T4, SQLSTATE-only exception wrapper) and `sync.doctor` | T2 |
| T4 | `sync/promote/` skeleton, options, exit codes, preconditions (phase 0) | T3 |
| T5 | Digest SQL generator, manifest diff, plan, the promote section of the report (`sync/report.py`), guards | T1, T4 |
| T6 | Apply: lock, snapshot batches, copy, per-image replace, files gate, `committed.json`, `setval` | T5 |
| T7 | Global sets: concepts, absorptions insert-or-close | T6 |
| T8 | Verify and `--verify-only` | T6 |
| T9 | **Deferred (decision 27, not built):** retire: pull precondition, ratings guard, `retired.json` (with `superseded`, `blocked`) | T6 (P4 for the real pull state) |
| T10 | First-seed mode | T6 |
| T11 | Integration scenarios, failure injection, report checks | T6 to T8, T10 |
| T12 | Docs (`docs/sync/promote.md`), CLAUDE.md pointer | all |

Size: large. Mode: subagent-driven, one task per fresh subagent (T0 to T4 overlap; T6 to T8 and T10 sequential). Two fresh reviews after T11: **data safety** (can any path write prod outside the rules,
promote a wrong environment, delete anything, half-promote, leak data) and **correctness vs the S3 ADR and the registry** (digest stability, order, absorptions, resume). No live database during development.

## Decisions made here (to confirm)

- Module layout, option names, counted flags, exit codes; batch size 500, report age 2 h, 25 % mass-change threshold. (Retire guard `max(50, 2 %)` and pull freshness 1 h are deferred with the retire.)
- Digest over all model columns with an explicit exclusion list, two summed hashes, pinned session settings.
- The files gate reads P3's report and compares `(id, filename, content_hash)`; `committed.json` is P2's result file; the plan lives in the report (`report.json`, informational, never an input).
- Absorptions on prod absent on dev are reported (left behind), never deleted; typed confirmation includes the server-read database name.
- In a `sync.run` dry run, images waiting for files are shown under `waiting_for_files` and counted in the expected totals and the plan hash; the apply moves any that did not get a file to `files_gate_miss` (to confirm).

## Risks and what is not settled

- The 25 % crossover and HNSW timings were measured on synthetic data; the first real seed (P7) is the test.
- CLIP and SBERT parity is a revision check, not a data check.
- The cross-design interfaces (P3 report, P4 pull state, P5 settings and grants, `sync/connect.py`) are fixed in `interfaces.md` and mirrored by those designs.
- Absorptions ship `loser_filename` and `loser_content_hash` of losers that never ship (rejected duplicates). Accepted for now; owner question below.

## Open for the owner

1. Absorptions carry the filename and hash of non-promotable losers to a public database (review M7; the statistics half is moot, decision 25): accept, or blank those two columns for non-promotable losers (adds a conditional transform to the digest).

## Deferred phase: removals (not built, decision 27)

Everything in this section is design text kept for later. None of it is in the "to build" lists, the flags table, the step order or the tests above. It was written when the retire was in scope and has not been re-reviewed since.

### Retire (decision 24, C2, H1, H3)

Image ids on prod that are not in dev's promotable set (flagged, cut, rejected, deleted on dev). `DELETE FROM images` cascades into `meme_text_ratings`, the one prod-origin table a promote can destroy.

1. **Always pulled first.** `sync.run` pulls (P4) inside the same run, right before the promote; a stand-alone `--retire` refuses unless `pull_state.json` has `applied: true`, `env` equal and `finished_at` younger than `--max-pull-age` (default **1 h**), with its `run_id`, `snapshot_at` and `ratings` fields present; under `sync.run` the pull's `run_id` equals the run's and the age rule does not apply. There is no `--i-have-pulled`.
2. **Ratings guard.** `sync_writer` receives `SELECT` on three columns of `meme_text_ratings` only: `(image_id, query_hash, updated_at)` (a P5 grant, read, so "no writes to class P" holds; the role test asserts exactly this). In each retire batch transaction, before the delete, P2 reads prod's `(image_id, query_hash, updated_at)` rows for the batch ids. The rule depends on `pull_state.json: ratings.mode`: (skip) an id with **any** prod rating is blocked, the run says "switch `sync.pull.ratings_mode` to `mirror` and pull"; no prod rating on the batch ids means the guard is trivially satisfied (the normal case while the ratings routes are off, decision 24); (mirror) an id is blocked when a prod row has `updated_at` after `snapshot_at`, or when the md5 of the sorted rows for the id differs from the same md5 over dev's mirror. A blocked id is not retired, is counted and listed in `retired.json` `blocked`, and the run says "pull again". A rating created in the milliseconds between this check and the delete remains a residual window (the FK takes a KEY SHARE lock, so the insert either precedes the cascade or fails); accepted, and moot while the ratings routes are off on prod (decision 24).
3. Guards as in Plan step 6, then batches of 500: `DELETE FROM images WHERE id = ANY(%s)` in the batch transaction.
4. **Result file (H3):** `<state dir>/runs/<runid>/retired.json` (fields in `interfaces.md` a) is opened with empty lists when the retire phase starts, written atomically **after each committed retire batch** (ids and filenames actually deleted) and, at the end, completed with `superseded` (the `renamed` entries of `committed.json`) and `blocked`. **P3's `files retire` reads only `retired.json`**, plus a fresh check that none of its ids is promotable on dev now; `plan.json` is never an input and `retire-result.json` does not exist. P4 may use the same file as its tombstone list for kept dev ratings of retired images (M6).
5. Reader rows (`meme_saves`, `meme_impressions`, `search_history`, `search_not_found`) are left alone, orphans by design.

### Other removal parts that were cut

- Flag `--retire`; flag `--allow-mass-retire N` (N equal to the planned count); flag `--max-pull-age D` (default 1 h); `retired.json` (`interfaces.md` a, deferred row).
- Guard "Mass retire": retire count above `max(50, 2 % of prod images)`, lifted by `--allow-mass-retire N`; the guard "Dev emptied" also covered "the plan would leave prod with zero images, also when retire is the only action"; the "Combined" guard counted `retire + changed` above 25 % as a mass change.
- Global sets replaced as a set (concepts: delete missing, upsert the rest) and the registry's `prune` field acted on; until then P2 ignores `prune`.
- After a retire: `VACUUM (ANALYZE)` of HNSW tables.
- Task T9 (retire: pull precondition, ratings guard, `retired.json` with `superseded`, `blocked`) and its integration scenarios (retire with cascade, pull-age precondition, ratings guard blocking, `retired.json` content equals what was deleted).
- When built: the report gains a `removed` category (and `left_behind` shrinks accordingly), and the P6 checks D2, D5 and F2 become errors or warnings again.

## Review disposition

| Finding | Disposition | Where / reason |
|---|---|---|
| C1 | adopted | Preconditions (running batch), Plan guards (mass change, regression, coverage), completeness floor |
| C2 | adopted (the `--allow-mass-retire N` part deferred, decision 27) | Handshake 1 to 8 (decision 18 identity checks, same-server, id overlap), `--allow-mass-retire N`, typed confirmation from the server, guards not lifted by the flag |
| H1 | adapted; **deferred with the retire (decision 27)** | `sync.run` pulls right before; `--max-pull-age` 1 h; ratings guard with a column-level `SELECT` grant on `meme_text_ratings` for `sync_writer` (P5); ms residual window accepted |
| H2 | adopted | Apply step 2: `(id, filename, content_hash)` comparison, `env` and `manifest_sha256`, 2 h. The "changed bytes" part follows decision 20 (always promoted) |
| H3 | adopted; **deferred with the retire (decision 27)** | `retired.json` written per committed retire batch; P3 reads only it; `plan.json` informational |
| H4 | adopted | Absorptions insert-or-close, per-loser ordering independent of timestamps, hard error on dangling `reversed_by_id` |
| H5 | adopted | Per-batch `REPEATABLE READ READ ONLY` dev transaction; `committed.json`; verify against promised digests |
| H6 | adopted | Phase 1 step 2 (pinned settings, explicit alphabetical lists, `ROW()::text`), column parity precondition, encoding |
| H7 | adopted | `memes.state` seeding/serving as database setting; first seed on the same resumable batch path |
| M1 | adopted | `image_extras` becomes `REPLACE_PER_IMAGE`; notes replaced whole |
| M2 | not adopted | Comics are class X (decision 22) |
| M3 | adopted | Two summed hashes instead of `bit_xor` |
| M4 | adapted | Bookkeeping columns of notes excluded; "second run empty" stated with dev frozen; `meme_profiles.updated_at` kept pending a check |
| M5 | adopted | No-leak wrapper, excepthook and sentinel test; prod's own log noted |
| M6 | adapted; deferred with the retire | `retired.json` offered to P4 as tombstone list; the ratings routes are off (decision 24), P4 decides |
| M7 | adapted | Statistics half moot (decision 25); absorptions question left to the owner, default accept |
| M8 | adopted | Completeness floor for new images |
| M9 | adopted, retire parts deferred | Combined guard, order images, globals, retire, no retire with `--limit-images` or deferred images, zero-image guard after plan (decision 27: the combined guard and the retire rules are in the deferred section) |
| M10 | not adopted here | A P5 matter (per-environment stack, decision 17); P2 only reads `pg_host`/`pg_port` and `local_port` from tracked settings. Noted as interface |
| Decision 26 (2026-11-12) | applied | Plan step 7 is the report (`interfaces.md` k): added, overwritten with changed digest groups, unchanged, skipped with reason, left behind, guards, estimate, plan hash and apply command; `--apply` re-plans and refuses on a hash difference; report tests added; `sync/report.py` in T2 |
| Decision 27 (2026-11-12) | applied | Retire phase, `--retire`, `--allow-mass-retire`, `--max-pull-age`, `retired.json`, prune and T9 moved to "Deferred phase: removals"; H1, H3 and C2 (the retire parts) deferred with them; orphans reported as left behind; pull no longer a precondition |
| Reconciliation 2026-11-12 | applied | `interfaces.md` adopted: shared modules assigned to T2/T3, handshake H1 to H10 (capabilities carry only `stage` and `environment`; revision and schema head come from the database), report age 2 h, ratings guard per `ratings_mode`, `retired.json` gains `superseded` and `blocked`, `plan-hash` is the one flag, T-numbers renumbered |
| L2, L3, L6, L7 | adopted | try-lock, `datname` filter, `setval` coalesce, exit 1 lists committed batches, broader role check |
| L4 | adopted | `images` upsert via `execute_values`, no staging or TEMP privilege |

### Deferred (low findings)

- L1: digest load on a serving prod (2 workers, `statement_timeout`, wall time reported) is set as a default; tuning waits for the P7 rehearsal.
- L5: whether any prod route serves `concept_image_sets.directory` files; check at implementation (rows without files).
- L8: prod drift is only reported against recorded digests (Verify); a standalone drift report belongs to P6.
