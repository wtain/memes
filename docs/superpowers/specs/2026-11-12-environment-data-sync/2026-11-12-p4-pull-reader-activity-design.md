# P4: pull reader activity, prod to dev (design)

Status: implementation (built on branch worktree-env-data-sync, unmerged, no real VPS, see tracker.md "Built state" 2026-11-16; text of the design: revised 2026-11-12; reconciled with `interfaces.md` 2026-11-12; revised again for decisions 26 (dry-run report) and 27 (no removals for now) on 2026-11-12)
Interfaces: state files, settings (`sync.pull.*`), roles, handshake steps, gate verbs, flags and exit codes are defined in `interfaces.md`; this document owns the pull behaviour.
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (registry, built), S3 (`docs/adr/adr-2026-11-12-dev-prod-sync-technique.md`, Q7), S4 (`docs/adr/adr-2026-11-12-dev-prod-file-sync.md`, Q4),
S5 (roles), P2 (shared modules, work item SC), P3 (gate), P5 (roles, ports), decisions log in `README.md` (decisions 6, 9, 10, 11, 13, 17, 18, 19, 21, 23, 24, 25, 26, 27).
Pairs with: P2 (no longer reads the pull-state: it only mattered to the retire, which is deferred, decision 27; pull and promote still run in one `sync.run`, pull first).

## Intent

Bring the readers' activity that accumulates on prod back to the dev database (and bug reports to a dev folder), so that dev sees the real use of the library: searches,
impressions, saves, not-found reports and, once the ratings routes return, the readers' ratings. Prod stays the system of record; dev holds a mirror that may be replaced at any time, with one
exception that the first review of this design found: ratings the owner made on dev (see Ratings). The pull runs in the same `sync.run` as the promote, immediately before it, as step 1. That ordering was chosen because retiring an image on prod cascades into its ratings; with removals deferred (decision 27) it is no longer a precondition of the promote, and the pull-state it writes is read by P6 and P7 (and by the retire when it is built).
Like every sync operation the pull is a dry run by default and produces a report of the rows that would be added and replaced on dev and the bug reports that would be fetched (decision 26, `interfaces.md` k).

## Goals / non-goals / anti-goals

- Goals: one consistent snapshot of the six class P tables; a mirror on dev that equals the snapshot for the append-style tables; bug reports pulled and removed on prod only after verification;
  a pull-state that P6, P7 and a future retire can trust (snapshot time, run id, ratings fingerprint); dry run by default with a report that the apply reproduces; idempotent; untrusted prod text handled as data only.
- Non-goals: pushing anything to prod, pruning prod (decision 11, with decision 23's alarms instead), merging dev-made activity (disposable, except ratings), uploads (disabled on prod),
  an incremental impression pull in v1 (backlog; a full mirror is under 3 s per 100k rows).
- Anti-goals: writing prod; losing the only copy of a rating; wiping dev because prod answered empty, was the wrong environment, or has its ratings routes off; logging row content; a pull that half-replaces dev;
  rendering, opening or executing anything that came from prod.

## Command

`python -m sync.pull --env {metal,general,it} [--apply] [--plan-hash H] [--allow-empty-source] [--allow-shrink TABLE]... [--allow-growth] [--skip-files] [--top N] [--run-id ID] [--json PATH]`

Dry run by default: it reads the prod snapshot and the dev tables, writes the report (next section) and changes nothing; `--apply` re-plans and refuses (exit 3) if `--plan-hash` differs from the dry run's printed hash. `--allow-shrink TABLE` names a class P table (repeatable), same shape as in P2. Exit codes as in `interfaces.md`: 0 clean, 2 precondition (including an identity mismatch or a held lock), 3 guard, 1 other. Called alone, or as the first step of `sync.run`, which passes `--run-id`.

### Connections (decisions 17, 18, 21)

- No environment variables and no hand-opened tunnel. The tool reads `environments/.env.<env>.sync` (gitignored, restricted permissions, never loaded in agent sessions) through the shared
  modules built by P2 (work item SC: `sync/config.py`, `sync/tunnel.py`, `sync/connect.py`), which open the `ssh -L` tunnel themselves from `sync.prod.ssh_alias`, close it in `finally`, and return a connection as the
  `sync_reader` role (`memes_<env>_sync_r`) of **this environment's** Postgres (per-environment roles, ports and keys; `interfaces.md` d).
- **Identity handshake first** (`interfaces.md` e; pull runs H1 to H6): `current_database()`, `current_user`, the database-level `memes.environment` setting, the capabilities `environment` and `stage` (HTTPS, public route)
  and, when bug reports are pulled, the gate's environment must all equal `--env` and the tracked expectations; otherwise exit 2 with a one-line reason and nothing read. The dev side must be the write-capable `DATABASE_URL` of `--env`
  (the read-only URL is refused) and its database must not carry a `memes.environment` setting. The alembic comparison (H10) is not run by the pull: column compatibility below replaces it.
- Prod side: one `REPEATABLE READ, READ ONLY` transaction for the whole pull, so the six tables, the image-id list and the ratings fingerprint are one snapshot. The role check refuses a connection that
  can write any table (`has_table_privilege`) or is the backend, owner or `sync_writer` role.
- Dev side: one write transaction for all six tables: a failure leaves dev unchanged. First statement: `SET LOCAL lock_timeout = '10s'`, then `LOCK TABLE` of the five tables written by the mirror
  in a fixed order in `SHARE ROW EXCLUSIVE` mode. This blocks dev writers (best-effort recorders of the running dev backend wait; readers keep reading the old state) and closes the race in which a
  concurrent dev insert takes an id that the copy brings (primary-key violation, rolled-back pull). A pull takes seconds; a lock timeout is exit 1 with "retry".

## Preconditions

- Column compatibility, not version equality: the column names and types of the six pulled tables (and `images.id`) are equal on both sides, read from `information_schema`. A differing `alembic_version`
  alone is a warning, because the driver pulls first and dev is usually one migration ahead of prod during a release (the additive migration goes to prod before the promote).
- The registry's pulled tables equal the six expected (`sync.registry.pulled_tables()`).
- Nothing else on dev needs to exist: the P tables hold no vectors.

## Dry-run report (decision 26, `interfaces.md` k)

`report.json` / `report.md` / stdout summary in `runs/<runid>/` (the pull section of a `sync.run` report). The dry run reads, inside the same prod `REPEATABLE READ, READ ONLY` snapshot, the keys and a per-row md5 (explicit column list, text cut by the same cap) of the six tables and compares them with the dev tables; no row text crosses into the report. Per table:

| Category | Content |
|---|---|
| `added` | rows that prod has and dev does not (to be inserted on dev): count, bytes, id list |
| `overwritten` | rows on both sides that differ (replaced on dev) plus, for the mirror's delete-then-copy tables, dev-only rows that the mirror discards (`dev_only_discarded`: dev-made activity is disposable; counted separately); for ratings in `mirror` mode the ledger-guarded partition (inserted, updated, deleted, kept-retired, skipped-unknown, archived) |
| `unchanged` | equal rows |
| `skipped_with_reason` | `skipped-unknown` ratings (image absent on dev), tables not touched (`ratings` in `skip` mode: "skipped: prod ratings routes disabled"), bug reports with an anomaly or a name or extension problem |
| `left_behind` | nothing on dev is left behind by design; rows kept on dev by the ratings rules (kept-retired, dev-made ratings) are listed here, informational |

Bug reports: `added` = files that would be fetched (count, bytes, the file names, which are `<timestamp>_<uuid>.<ext>` and carry no text), `skipped_with_reason` = not settled (younger than 60 s), anomalies, `to_delete_on_prod` = the files that would be deleted on prod after a verified pull (count; decision 13, the only thing the pull ever deletes on prod and only bug-report copies it received).
Also in the report: the guards (empty source, shrink, growth, row ceiling) with value, limit and tripped or not, the truncated-value count of the text cap, the ratings mode and fingerprint, handshake and precondition results, the estimated duration and bytes on the wire (from the row counts and `COPY` rates), the plan hash and the exact apply command.
In `report.json` the id lists of the large append-style tables (impressions, history) are complete up to `sync.report.max_ids` (default 100000) per category; above it the list is replaced by `ids_truncated: true` with the count and the id range (a report of millions of ids helps nobody).

## Mirror semantics per table

Source = the prod snapshot. Order inside the single dev transaction (children first on delete, last on copy):

| Table | Strategy | Notes |
|---|---|---|
| `search_history_tags` | delete all, copy last | child of `search_history` |
| `search_history` | delete all, then `COPY` | UUID ids, no merge |
| `search_not_found` | replace | FK-less |
| `meme_saves` | replace | FK-less |
| `meme_impressions` | replace, then `setval` | bigint serial; `setval(seq, max(id))`, or `setval(seq, 1, false)` when empty. Sequences are not transactional: a rollback leaves the sequence ahead of the data, which is harmless |
| `meme_text_ratings` | staging, ledger-guarded mirror | see Ratings; mode `skip` while prod's ratings routes are off |

`COPY ... (FORMAT binary)` from the prod snapshot into dev, reading through `COPY (SELECT ...)` so free-text columns are cut at a cap (below). Removal is `DELETE` for the small tables.
`TRUNCATE` is used only for the FK-less `meme_impressions`, `meme_saves` and `search_not_found` once their dev row count passes a tracked threshold (default 200k): it is transactional in Postgres, avoids
dead-tuple bloat from deleting millions of rows, and the `SHARE ROW EXCLUSIVE` lock is already held, so the stronger `ACCESS EXCLUSIVE` it needs (which would also block dev readers for the copy's duration, seconds)
is the only extra cost. Plain `TRUNCATE` is never used on `search_history` (it is an FK parent; the `CASCADE` form would also hit unrelated tables).

### Untrusted text (the P5 review, M4)

Everything from prod is data written by anonymous readers. Rules for the pull: (1) a per-field cap on every free-text column (search text, facet values, not-found text, rating text; the list comes from the registry,
default 2,000 characters, tracked setting), applied by `left()` in the source `SELECT`, with the count of truncated values reported; (2) a row ceiling per pull (see Guards); (3) the tool never interprets
the text: it does not log it, render it, `eval` it, shell out with it or use it to build SQL (all values travel as binary `COPY` data or bound parameters); (4) control characters are kept as they are in the database (changing
them would break the mirror equality), and the documentation states that **every consumer that prints or exports this text must strip control characters and neutralise spreadsheet formulas**
(`tools/discovery_report`, the evaluation export; both are named in P5's review as follow-ups and tracked in P7's docs task, not built here).

### Ratings (the only P table with a foreign key to `images`, and the only one dev also writes on purpose)

The first review of this design found two problems with a plain mirror. **(a)** Decision 24 turns the ratings routes off on prod while `/reply` is off, so prod's ratings table is empty or frozen while the
owner rates results on dev: a plain mirror would delete every dev rating at each pull. **(b)** P2 review M6: a rating kept for a retired image is lost when the image returns. So:

1. **Mode.** `sync.pull.ratings_mode` (tracked setting): `skip` (default) or `mirror`. In `skip` the dev table is not touched, and the report says "skipped: prod ratings routes disabled" (read from the capabilities
   `features.ratings`, and checked to be consistent with the snapshot: a non-empty prod table with the routes reported off is a warning). The prod snapshot is still **read** for the fingerprint below
   (count, max `updated_at`, key hash; no text), because the retire guard (deferred, decision 27) will need it in both modes and P6 reports it. The owner sets `mirror` in the same change that switches the ratings routes on for prod.
2. **Mirror, when on.** A local ledger `<state dir>/ratings_ledger.json` holds the hashes of `(query_hash, image_id)` keys that a previous pull received from prod (no text). Rows are:
   inserted when prod has them and dev does not; updated where `grade` or text differ (prod wins, counted); **deleted on dev only if the key is in the ledger and prod no longer has it** ("Forget text" on prod is mirrored);
   dev rows whose key was never received from prod (made on dev by the owner) are never deleted, and are not overwritten except when prod has the same key.
3. **Skipped:** prod rows whose `image_id` does not exist on dev (dev deleted the image first), counted.
4. **Kept:** dev rows whose `image_id` is absent from prod's `images` (never promoted, or retired there once removals exist) are kept. The snapshot reads `select id from images` (the P5 role list gives `sync_reader` `SELECT (id)` on `images`).
5. **Never silently dropped:** before a ratings row is deleted or overwritten on dev, it is appended to `<state dir>/ratings_archive.jsonl` (restricted permissions, local, the one file that holds rating text). This also covers the
   returning-image case of P2 review M6: the image comes back, prod has no rating, the next mirror would delete the kept dev row, and the archive retains it. Restoring from the archive is a manual step (backlog).
6. The report counts: inserted, updated, deleted, kept-retired, skipped-unknown, archived.

## Guards

- **Empty source:** the prod snapshot has zero rows in every class P table while dev has rows: refuse unless `--allow-empty-source`. (The handshake already rules out the wrong environment.)
- **Shrink refuses, not warns.** Prod never prunes (decision 11), so `meme_impressions`, `meme_saves` and `search_not_found` can only grow; any count below the previous pull's (from the pull-state file) is a refusal
  (restore from backup, the wrong database, data loss on prod) unless `--allow-shrink`. `search_history` and `meme_text_ratings` can lose rows by "Forget text": a drop over 50 % is a refusal, a smaller drop a count in the report.
  The first pull has no baseline; its only guard is the empty-source rule. A lost state file makes the next pull a first pull (reported).
- **Growth (decision 23):** a table that grows over `sync.pull.max_growth_factor` (default 5x, minimum absolute growth 100k rows) since the previous pull, or an absolute row ceiling per pull, is a refusal without
  `--allow-growth`: a flood must not become gigabytes in the dev database silently. The same numbers feed the growth alarm of the verify report.
- **Locks:** a pull of an environment is refused if another sync command holds the state-directory `lock` file (`interfaces.md` a; `sync.run` holds it for its whole run and passes it down); and while a dev batch run is active (a `batch_runs` row in the started state) it only warns
  (the pull writes only class P tables, which no batch job writes).

## Pull-state file (consumed by P6 and P7; by the retire once it is built)

`<state dir>/pull_state.json`, written atomically **right after the dev transaction commits**, and updated atomically again after the bug-report step (so a bug-report problem does not block a retire):

```json
{ "env": "general", "run_id": "...", "finished_at": "2026-11-12T19:55:00Z",
  "snapshot_at": "2026-11-12T19:54:41Z", "snapshot": "<pg_current_snapshot()>", "applied": true,
  "ratings": {"mode": "mirror", "prod_count": 38, "max_updated_at": "...", "keys_sha256": "..."},
  "prod_image_ids": 18087,
  "tables": {"meme_impressions": {"prod": 91234, "replaced": 91234}, "...": {}},
  "bug_reports": {"status": "done|partial|skipped", "pulled": 3, "deleted_on_prod": 3, "failed": 0} }
```

**Deferred (decision 27):** P2's retire will require `applied: true`, `env` equal, and either the same `run_id` (the normal case: pull and promote in one `sync.run`) or `finished_at` younger than `--max-pull-age` (default **1 hour**, P2 review H1). Today nothing in P2 reads this file;
P6 (check P1) and the P7 driver do. The `ratings`
block (`mode`, `prod_count`, `max_updated_at`, `keys_sha256` = sha256 of the sorted `image_id|query_hash` lines of the snapshot) is what the retire guard will read together with `snapshot_at`: in `skip` mode any prod rating on an image about to be deleted blocks that
image ("switch to `mirror` and pull"), in `mirror` mode a prod rating newer than `snapshot_at`, or a key set that differs from the dev mirror, blocks it (the pull must run again). A dry-run pull never writes the file. When the ratings routes are off on
prod and the table is empty (`prod_count` 0), the guard is trivially satisfied.

## Bug reports (file part)

Uploads are disabled on prod, so there is no inbox; bug reports are the only files. Kept on prod, size-capped before the body is read and rate-limited (decisions 13, 23), named `<UTC timestamp>_<uuid><ext>` (unique),
written to prod's bug-reports directory. They are anonymous, untrusted bytes: the pull never opens, parses, thumbnails or executes them. Transport = the P3 gate through the **verb** key (`sync.prod.gate_alias`; decision 19: the upload account is staging-only); the pull uses tar on the ssh stdout.
1. `pull-list`: names, sizes, hashes of flat, regular, non-hidden files whose modification time is older than 60 s (a file still being written is not listed; P5 should have the backend write to a temporary name and rename, assumed). Anything that is not a regular file is reported as an anomaly, not pulled.
2. `pull-tar`: the named files as a tar on the ssh **stdout** (the fast direction on Windows), read with `tarfile` in stream mode and never `extract`/`extractall`: regular members only, no links or special files, names must match the
   listed names and the pattern `^[0-9TZ_-]+_[0-9a-f-]{36}\.[a-z0-9]{1,8}$`, member size equal to the listed size and under the per-file cap (10 MB) and the per-pull cap (count and bytes, tracked), no duplicate names. Each file is written by the tool to a temporary file and its sha256 compared with prod's.
3. Publish into dev's `BUG_REPORTS_PATH` (default `BASE_PATH/bug_reports`) with a no-clobber rename after `fsync`; a same-named file with the same hash counts as received; a same name with a different hash is stored with the next `_n` suffix. Extensions outside an allow-list (`.txt .json .png .jpg .log .zip`, tracked) are stored as `.bin`.
4. A ledger `{name: sha256}` (`bugreports_ledger.json` in the state directory) records receipt. Only then `delete-verified` removes the file on prod, re-checking that its hash still equals the received one (a replaced file survives). A name that re-appears on prod with a hash other than the ledger's is a new file.
5. Crash between publish and delete: the next run finds the name in the ledger and deletes on prod without a second download. A lost ledger only costs a re-download: the no-clobber publish with the same hash is a no-op.
Gate verbs owned by this design, implemented in P3's `memesync-gate.py` (`interfaces.md` g): `pull-list`, `pull-tar`, `delete-verified` (names NUL-separated on stdin, validated by the pattern above, restricted to the bug-reports directory of **this** environment, which the gate fixes from the key). Bug-report text is never decoded by the tool.

## Failure behaviour

| Event | Result |
|---|---|
| Identity handshake mismatch | exit 2 before any read; nothing on dev changes |
| Tunnel drops during the table copy | the dev transaction rolls back; dev unchanged; re-run |
| Dev backend writes during the pull | its writes wait on the table lock (up to `lock_timeout`), then land after the commit and are replaced by the next pull; the dev transaction never blocks readers |
| `sync.run` killed after the pull, before the promote | tables final; pull-state present (`applied: true`); the next `sync.run` pulls again (cheap) and continues |
| Crash after the dev commit, before the pull-state write | the next pull is a normal full mirror (the missing state only matters to P6's age line and, later, to the retire) |
| Dev changes between the dry run and the apply | `--plan-hash` differs: exit 3 before any write; re-read the new report |
| Hash mismatch or hostile tar member on a bug report | that file is not published and not deleted; the run reports it by name pattern only and continues; exit 1 at the end; the pull-state says `partial` |
| Ratings fingerprint changes between the snapshot and the retire | Deferred with the retire (decision 27): P2 will stop the retire for the affected ids and ask for a new pull (P2's rule; this document provides the fingerprint) |

## Privacy

Search text, message text and rating text flow to dev, the owner's own machine. The tool never prints or logs row values: counts, table names and ids only; exceptions are caught and reported by class and SQLSTATE
without the driver's `DETAIL` text (P2 review M5). The `--json` report has counts only. The ratings archive is the only file with rating text and lives in the restricted state directory.
Prod keeps the originals; dev's copy of the append-style tables can be wiped by the next pull.

## Tests

1. **Unit:** report completeness and reproducibility (every category per table, guards, estimate, plan hash and apply command present; the same snapshot gives the same hash; a prod row that changes between dry run and apply does NOT change the structural plan hash (owner decision A, 2026-11-14), a changed flag, mode or guard state does and makes `--plan-hash` exit 3; the dry run changes nothing on dev), redaction (a sentinel in a search text, rating text or bug-report body never reaches `report.json`, `report.md` or stdout; ids, counts and report file names only), id-list truncation above `sync.report.max_ids`, table plan, ratings partition (insert/update/delete/kept-retired/skipped-unknown/archived from in-memory fixtures; `skip` vs `mirror` mode), ledger-guarded deletes, guard logic (empty source, shrink, growth, state-file loss),
   pull-state schema and atomic two-stage write, bug-report ledger, name pattern, tar member validation (hostile members: absolute and `..` names, links, devices, oversize, duplicates, name not in the list), text cap.
2. **Integration** (real Postgres, two databases, the P2 fixture): the Q7 scenarios of the S3 ADR; ratings of an image unknown on dev; of an image retired on prod kept; **dev-made rating survives a pull with an empty prod ratings table**; an archived rating after a prod "Forget";
   FK order; `setval` then a default insert on `meme_impressions` (also empty table); concurrent dev insert during the pull waits and does not collide; handshake mismatch (wrong database, role, marker, environment) exits 2 with no dev change;
   empty-source refusal and `--allow-empty-source`; shrink refusal; growth refusal; idempotent second run; snapshot consistency (a writer on the source during the read does not tear the tables); `sync_reader` cannot write (the executed SQL contains no write against prod); a column mismatch refuses, an alembic-only mismatch warns.
3. **LocalTransport** (no ssh): bug reports: pull, hash mismatch, hostile tar, publish collision, crash between publish and delete and resume, hidden, in-progress and non-regular files ignored, replaced file survives the delete.
4. **Docker ssh test** (marker `docker`, local only): the bug-report pull over a real ssh with the two-account setup.
5. **Not covered:** a real network, a real VPS, millions of impression rows (the watermark option is the answer, not built).

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | `sync/pull/plan.py`: table plan, ratings partition and ledger, guards (pure) with unit tests | P1 |
| T2 | Use of the shared modules: handshake H1 to H6, role check, snapshot reader, dev locks, column compatibility check | P2-T0 fixture, P2-T3 (SC: `sync/connect.py`) |
| T3 | Mirror apply for the five simple tables, text cap, `setval`, TRUNCATE threshold | T2 |
| T4 | Ratings modes, ledger, archive, fingerprint | T3 |
| T5 | Two-stage pull-state file and the dry-run report (pull section of `sync/report.py`, plan hash, `--plan-hash`) | T3 |
| T6 | Gate verbs `pull-list`, `pull-tar`, `delete-verified` and the bug-report step (LocalTransport tests) | P3 T3, T4 |
| T7 | Integration scenarios; Docker ssh test | T3 to T6 |
| T8 | Docs (`docs/sync/pull.md`: including the "consumers must sanitise" rule), CLAUDE.md pointer, grant list for P5 (`SELECT (id)` on `images` for `sync_reader`) | all |

Size: medium. Mode: subagent-driven; T1, T6 can start early. One review after T7: **data loss and privacy** (can the pull destroy a rating, wipe dev, leak text into logs, write prod, act on hostile text or tar) and **correctness against the S3 Q7 findings**.

## Revision notes (2026-11-12, after the reviews of P2, P3, P5 and the owner's decisions 17 to 25)

| Finding / decision | Action |
|---|---|
| Decisions 17, 18, 21 | the ambient database-URL variable and the hand tunnel removed; `.env.<env>.sync`, shared connection modules (SC), tool-owned tunnel, per-environment roles; identity handshake as the first step |
| Decision 19 | bug-report pull through the verb account, tar on stdout, strict member validation, no `extract` |
| Decision 24 | ratings table stays class P; `ratings_mode: skip` while prod's routes are off |
| Decision 25 | statistics are not a pull topic; nothing to change except removing them from any report text |
| Own finding: the plain ratings mirror deletes the owner's dev ratings when prod's table is empty (routes off) | mode `skip`; ledger-guarded deletes; archive before delete or overwrite |
| P2 M6: kept rating lost when the image returns | archive file; manual restore; owner question kept |
| P2 H1: ratings lost between pull and retire | pull and promote in one `sync.run`; pull-state carries `run_id`, `snapshot_at`, ratings fingerprint; freshness 1 h; state written right after the dev commit |
| Own finding: concurrent dev inserts collide with copied ids | `SHARE ROW EXCLUSIVE` locks with `lock_timeout` before the copy |
| Own finding: DELETE of millions of rows bloats; TRUNCATE locks | TRUNCATE for the FK-less tables above a threshold; never for `search_history` |
| Own finding: alembic equality blocks the pull during a release | column compatibility check instead; alembic difference is a warning |
| Own finding: 50 % tripwire is only a warning and misses small losses | append-only tables refuse any shrink; 50 % is a refusal for the other two; `--allow-shrink` |
| P5 M4 / decision 23: untrusted text and flood | per-field cap, growth guard `--allow-growth`, consumers-must-sanitise rule, no interpretation |
| Own finding: bug-report listing can catch a half-written file; bug-report failure blocked the state | 60 s settle rule, rename-after-write assumption; state written in two stages |
| P2 M5: error text leaks values | class and SQLSTATE only |
| Decision 26 (2026-11-12) | dry-run report section (added, overwritten, unchanged, skipped, kept; bug reports to fetch and to delete after verification), `--plan-hash`, `--top`, report tests |
| Decision 27 (2026-11-12) | the pull stays step 1 of `sync.run` but is no longer a precondition of the promote; the pull-state is read by P6/P7 only; the retire-related freshness rule and ratings guard use are deferred |
| Owner decision A (2026-11-14): the plan hash is structural | The `sync.pull` plan hash covers only the plan's STRUCTURE: the tables in scope, the ratings mode, the flags (`skip_files`, `bug_reports` on or off, `text_cap`) and each guard's id and tripped state (the guard values and limits are measurements, not hashed). It does NOT cover prod row ids, row digests, counts or the bug-report lists: readers write to prod every minute, so a hash bound to prod content could never survive the time between a dry run and its apply (and `sync.run` binds this hash into its combined one). Those stay in `report.json` / `report.md` as before, which the owner reads. Built as `Section.structural` (`sync/report.py`) and `plan.build_plan` (`sync/pull/plan.py`, the former `content_digest` and the `rows_<table>` flags are gone); the apply copies what prod holds when it runs. A changed flag, mode or guard state still makes `--plan-hash` exit 3. The test sentence "a changed prod row between dry run and apply makes `--plan-hash` exit 3" is replaced by "does not". Stand-alone typed confirmation stays `<env> <dev database>` (decision B) |
| Reconciliation 2026-11-12 | `interfaces.md` adopted: shared modules built by P2 (SC), `--allow-shrink TABLE`, fingerprint also in `skip` mode, `pull_state.json` fields as in `interfaces.md` a, bug-report ledger name, state `lock` file instead of an advisory lock, verbs table shared |

## Choices made by me (to confirm)

- Full mirror every time in v1; the impressions watermark stays backlog.
- Ratings default to `skip` with a ledger and an archive; the owner flips `ratings_mode` to `mirror` together with enabling the routes on prod.
- Pull-state freshness for a retire (deferred): same `run_id`, else 1 hour.
- Shrink of an append-only table is a refusal; the growth factor 5x and the 200k TRUNCATE threshold are tracked settings.
- Dev's own activity rows (except ratings) are replaced; the pull needs the `lock_timeout`-bounded table lock, so dev searches may wait a few seconds during a pull.
- Bug reports are deleted on prod after a verified pull (decision 13 read literally).

## Risks and what is not settled

- A search made on dev during the pull is lost at the next pull: intended (disposable), now visible in the report only as "replaced".
- `search_history` deletions on prod ("Forget text") are mirrored; the pulled copy is not an archive.
- If prod's `images` grants for `sync_reader` are refused by the owner, the retired-image rule cannot be evaluated and ratings stay in `skip`/insert-only mode.
- The ratings ledger lives in the state directory; losing it makes every dev rating "dev-made" (never deleted), which is the safe direction.

## Open for the owner

None blocking. Two choices to confirm: the default `ratings_mode: skip` and the archive file for rating text (P2 review question 2: keep, restore on prod or drop a kept rating when its image returns; this design keeps and archives, never restores to prod).
