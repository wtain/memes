# Review: P2 promote data design (fresh design reviewer)

Status: done

Subject: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p2-promote-data-design.md`, read against the epic README (16 decisions), P3, P4, S1, S5, the S3 ADR, `sync/registry.py`, `sync/model.py`, `docs/sync-registry.md` and `Storage/models.py`. Design review only; no code, no database, no network. "P2 section" below means the section heading of the design under review.

Counts: critical 2, high 6, medium 10, low 8.

---

## Critical

### C1. Nothing stops a promote from replacing prod's derived data with a half-built or emptied dev (P2 section "1. Plan" step 5, Guards; "Apply" step 3)

Failure scenario. The three dev environments run continuously with batch jobs. CLAUDE.md documents that a full `build_ocr_lemmas` deletes the whole table first and that a crash (the cp1252 `UnicodeEncodeError`) left a *committed partial rebuild*: 1,800 images with lemmas, 15,800 with none. The same shape exists for `build_image_embeddings` full, `build_tags_from_*` full runs ("Full run deletes every Ollama tag first"), `build_image_descriptions --reset`, `build_meme_profiles --reset`. If the owner runs a promote while one of these is running, or after one died half way, the digests of tens of thousands of images differ ("changed"), each is "replaced whole", and prod loses its lemmas, tags or vectors for those images. The only guard on `new + changed` is the 25 % *advice* (explicitly "not a refusal"). The retire guards do not apply, since no image leaves. This is the "wipe prod because dev looked wrong" path, and it is not covered by any of the three named guards. It also bypasses the empty-source guard because the images and their other tables are present.

Suggested change.
1. Refuse (exit 3) when dev `batch_runs` has any row with `status = 'started'` (the tool already reads dev; the unique partial index on `batch_runs` makes this one cheap query). Make it a precondition, lifted only by `--ignore-running-batches`.
2. Add a per-table regression guard, computed from the manifests (counts are already in them): for each shipped table, compare `sum(count)` over the *changed + unchanged promotable* images on dev with prod's count for the same ids. A table whose total on dev is lower than on prod by more than max(100 rows, 2 %) is a hard guard (exit 3, table named), and any single image that loses *all* rows of a table it has on prod is listed and counted; more than 1 % of images doing so is also a hard guard. Rows may legitimately shrink (a shorter lemma list), but never by thousands at once. Lifted per table with `--allow-shrink TABLE`.
3. Make the 25 % rule a hard guard for `changed` (not `new`) on a serving prod, lifted by `--allow-mass-change`. A systematic digest bug (see H5) otherwise silently turns every run into a full replace of a live database.
4. Completeness floor for new images (see M8).

### C2. The tool cannot tell which prod it is talking to, and `--allow-mass-retire` lifts the only barrier (P2 sections "Connections and credentials", "Guards", "Retire")

Failure scenario. The target is whatever `PROD_SYNC_DATABASE_URL` happens to contain in that shell, three environments times two roles, set by hand. Typical slip: `--env general` with the URL of the `metal` prod database (same host, same role name, same schema, same alembic head, so every precondition passes). The ids are disjoint, so the plan says: retire all of metal's 18,087 images, add 32,314 new ones. The retire guard trips (exit 3). The owner reads "mass retire" as "yes, dev has been cleaned up", adds `--allow-mass-retire`, types `general` at the confirmation (which compares against the `--env` argument, not the target), and the promote deletes the other environment's entire corpus, and its cascade deletes every `meme_text_ratings` row there, which is prod-only data with no copy (the pull would have to have run against that same wrong target to have saved it). The same slip with `--first-seed` fills the wrong empty database and then refuses to be re-seeded ("refused if prod has any image"). A second variant: the tunnel port is wrong and the URL ends up pointing at the dev Postgres itself (port 5432 is occupied by `ocr-db`), which is "promote dev to dev".

Also, the text is ambiguous about whether `--allow-mass-retire` lifts the other two retire-related guards (zero images, promotable smaller than half of prod). It should not.

Suggested change.
- Establish target identity from two independent sources and compare (see "Configuration and tunnel: recommendation"): tracked expected database name and stage marker, versus what the server reports (`current_database()`, `COMMENT ON DATABASE` marker set by the P5 provisioning script, role name).
- Add an overlap check that cannot be lifted: if prod already has images, at least half of prod's image ids must exist in dev's `images` table (any status). A wrong-environment pairing has an overlap of 0 and is refused whatever flags are given.
- Refuse when dev and prod connections are the same server (compare `pg_postmaster_start_time()` plus the database oid, plus `pg_control_system()` system identifier if the role may call it).
- Replace the boolean `--allow-mass-retire` by `--allow-mass-retire N` where N must equal the planned retire count, so a flag typed from habit does not fit a different plan; keep "zero images", "dev smaller than half of prod" and the overlap check as separate hard guards that this flag does not lift.
- The typed confirmation should be `<env> <prod database name>` read back from the server, not from the argument.

---

## High

### H1. Ratings created between the last pull and the retire commit are destroyed (P2 section "Retire", step 1; P4 pull-state; S3 Q4)

Failure scenario. The precondition is "a pull finished within 24 h". A reader rates an image at 10:00; the pull ran at 09:00; the owner retires that image (flagged on dev) at 11:00. `DELETE FROM images` cascades the 10:00 rating away. It never reached dev, and "prod is the system of record, keep everything" (decision 11). The same race exists at second granularity (rating insert and retire concurrently; the FK check takes a KEY SHARE lock, so the insert either errors for the reader or is committed and cascaded). The freshness window turns a design meant to be loss-free into a window of up to a day.

Suggested change. Grant `sync_writer` `SELECT` on `meme_text_ratings` only (read, so the "no writes to class P" rule stays intact; has to be added to S5 and to the role test) and, inside the retire transaction, before the delete, run `SELECT image_id, query_hash, updated_at FROM meme_text_ratings WHERE image_id = ANY(batch)` and compare with dev's mirror (`(query_hash, image_id)` rows with the same `updated_at`). Any rating on prod that dev does not have identically stops the retire for those ids (report ids and counts only) and tells the owner to pull again. Also require the pull to be fresher (1 h by default) and make the pull state carry the pull's snapshot time, so "rated after the snapshot" is checkable. If giving `sync_writer` `SELECT` on a class P table is unwelcome, the alternative is a 60-second pull-then-retire sequence run by one command; the check above is cheaper and stronger.

### H2. The files gate is keyed by image id only, and the P3 report can be a day old (P2 section "Apply", step 2; P3 "The report P2 consumes")

Failure scenario. The report lists `{id, filename, size, content_hash}`. P2 only tests "is the id in the newest report not older than 24 h". Dev changes between the report and the apply (the owner runs `fix_image_formats`, which converts a WebP to JPEG *in place* with a new `filename` and `content_hash`; or a recut approval; or `detect_file_duplicates`): P2's digest sees `core` changed and writes the new filename and hash to prod, while the report (and the file on prod) still carry the old name. The row is visible, the file is not: the anti-goal. P3 refuses changed bytes by default, but it is the *row* that gets changed here, and P3's refusal only protects the next P3 run.

Suggested change. For each image about to be committed, P2 compares the dev row it is about to copy (`filename`, `content_hash`, `width`, `height` if relevant) with the report entry and skips on any difference (reported, not committed). State this in the gate text. Lower `--max-report-age` to a few hours, and require `report.env == --env` and `report.manifest_sha256` to match the promotable manifest P2 recomputes at start (cheap: ids and hashes). Also write down that a P3 `retire` between report and apply invalidates the report (the report should be deleted when `retire` runs).

### H3. P3 `retire` consumes P2's *plan* retire list, not the result (P2 section "1. Plan" step 6; P3 "retire flow" step 1)

Failure scenario. `plan.json` is written by `--json` at plan time and carries the retire list. P3's retire checks orphans against "the P2 plan's retire list (newest)". If the retire phase did not run (no `--retire`, guard tripped at exit 3, a batch failed, the owner answered no), or a later dry run wrote a newer `plan.json`, P3 quarantines the files of images whose rows are still live on prod. Rows without files, again.

Suggested change. P2 writes a second file after the retire commit: `runs/<runid>/retired.json` with the ids and filenames actually deleted (written per committed retire batch, appended atomically), and P3 `retire` reads only that file, not `plan.json`, plus a fresh check that no id in the file is promotable on dev now. State in P2 that `plan.json` is informational. Dry runs never write the state used by P3.

### H4. `popularity_absorptions`: the update path of an already-shipped row is missing, and the load order has holes (P2 section "Apply" step 4; "1. Plan" step 4; `models.py` lines 302 to 342)

Failure scenarios.
(a) The text says absorptions are "append-only: planned as inserts of ids missing on prod". But the only legitimate mutation of an existing row is `UPDATE ... SET reversed_by_id` on an absorb row when it is reversed later (unflag, undo-reject, abort, correction). A reverse row is "missing on prod", so it is inserted (FK `reverses_id` to the absorb row, present), but the already-shipped absorb row on prod is never updated. Prod keeps an open absorb whose reverse row exists: the popularity breakdown is wrong, and `uq_popularity_open_loser` stays occupied.
(b) Then, when dev later re-absorbs the same loser (a new absorb row for the same `loser_image_id`), the insert on prod violates `uq_popularity_open_loser` (kind = 'absorb', `reversed_by_id IS NULL`, same loser) because (a) left the old one open. The promote fails in the global phase, after the image batches have committed, and fails again on every run.
(c) "In created_at order": `created_at` is `server_default now()`, which is the *transaction start* time, so an absorb row, its reverse row and a following absorb of the same loser written in one dev transaction share one timestamp. Order is then arbitrary.
(d) With two reversed absorbs of the same loser (possible after two undo cycles), inserting both with `reversed_by_id = NULL` before closing the first one violates the same partial unique index; only strict interleaving avoids it.

Suggested change. Treat the table as insert-or-close, not insert-only: plan = rows missing on prod, plus rows present on both where dev has `reversed_by_id` set and prod has NULL (compared by row hash with `created_at` normalised to UTC). Per loser, process absorbs with `reversed_by_id` set first (each as absorb with NULL, its reverse row, then the update), and the single open absorb last. That is correct without relying on timestamps because dev's own unique index allows at most one open absorb per loser. Order across losers is irrelevant (no FK to images). Put the three rows of a pair in one transaction. Test: absorb, reverse, re-absorb across two promotes; two reversed plus one open of one loser in one promote. Also add to the shape check: an absorb with `reversed_by_id` pointing at a row absent from the dev set is a hard error, not a skip.

### H5. The dev side is read without a snapshot while dev pipelines run, so a batch can be torn and a verify can lie (P2 section "Connections", "Apply" step 3, "3. Verify")

Failure scenario. Digests (plan) and each batch's `COPY (SELECT ...)` are separate statements in autocommit; a dev job commits a new description between the `image_descriptions` copy and the `description_lemmas` copy of the same batch. Prod receives descriptions without lemmas (or an embedding for a description row that was just replaced), inside a transaction that the design calls consistent. The next run repairs it, but until then the image is visible incomplete; and the plan's "changed" decision was made on different data than the copy. The verify at the end, which recomputes the dev digest *now*, will report differences for any image dev touched during the run (exit 4 for no real reason), and cannot tell a bug from a moving dev.

Suggested change. One dev transaction per batch: `BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, recompute the batch's digests inside it, compare with the plan, re-check `PROMOTABLE_IMAGES_SQL` for these ids, then do all `COPY TO` reads in the same transaction; commit it after the prod commit. (Per-batch, not run-wide, to avoid holding back vacuum on dev for the whole run.) Record each committed image's digest in the run's state file; verify then compares prod against the recorded digests (what was promised), and separately reports "dev changed since" as information, not exit 4.

### H6. The digest can be unstable across the two instances; the design leaves the dangerous parts to the implementer (P2 section "1. Plan" step 2; S3 Q3)

The S3 measurement ran both containers on one host with identical settings, so it could not show the following.

- **Session formatting.** `row::text` of a `timestamptz` depends on the session `TimeZone` and `DateStyle`. `popularity_absorptions.created_at`, `comics.created_at/built_at` and `statistics_snapshots.computed_at` are `timestamptz`. A dev Docker server at `Etc/UTC` and a VPS at another zone give different text for identical instants: those rows are "changed" forever (a perpetual update plan for absorptions and comics, so the "second run plans nothing" claim fails, and with comics in the `comics` digest group, built images are replaced on every run). Floats: `extra_float_digits`, `IntervalStyle`, `bytea_output` also matter. Fix: set on every digest session `SET TimeZone='UTC'; SET DateStyle='ISO, YMD'; SET extra_float_digits=1; SET bytea_output='hex'; SET client_encoding='UTF8'` and assert them back with `SHOW`.
- **Column order and `NULL`.** "Row content" is unspecified: `t::text` depends on physical column order (a dev database built from `baseline.sql` plus a long migration history can differ from a prod built by one clean `alembic upgrade head` when any migration was edited, or a column re-added); `concat_ws` drops NULLs so `('a',NULL)` equals `(NULL,'a')`. Specify: an explicit column list from the registry in alphabetical column order, `ROW(c1, c2, ...)::text` (distinguishes NULL from empty string), vectors via `::text`, `json` via `::text` (verbatim, stable), never `*`. Add a precondition that the column lists (`information_schema.columns`: name, data type, nullability) of the shipped tables are identical on both sides, which also protects the explicit-column `COPY` and catches a hand-edited dev database that the alembic version check cannot.
- **Collation and encoding** do not matter for the hash (byte-based) but do for a prod database in a non-UTF8 encoding: assert `server_encoding` equal and `UTF8`.

### H7. First seed is not resumable and breaks the one invariant it should keep (P2 section "First seed and index state", "Failure and resume")

Failure scenario. `--first-seed` is "refused if prod has any image", loads "single transaction per table, level order". A crash after `images` and before `ocr_lemmas` leaves a database that has images and not their rows; re-running is refused (prod has an image); the normal path would repair it but is a different mode with a different performance profile (live indexes). There is also no definition of the "serving marker" it relies on (P5 has none), and "image visible without its derived rows" is allowed during the seed on the argument that nothing serves.

Suggested change. Define the marker concretely (for example the existence of a row in `statistics_snapshots`, or better a database-level marker set and cleared by the owner script: `COMMENT ON DATABASE ... 'memes-sync:stage=prod;env=general;state=seeding|serving'`, which needs no schema change and doubles as the identity marker of C2). `--first-seed` is allowed while `state=seeding`, is idempotent (it computes the same manifest diff and continues by image batches, just without the drop-and-rebuild cost model), and the owner sets `state=serving` as the last step after the index rebuild. Keep batches (images with all their rows), not tables, even in seed mode; the index-less cost is the same.

---

## Medium

### M1. `image_extras` is upserted, so a removed remark never leaves prod (registry `image_extras` = UPSERT; P2 "Apply" step 3)
The `core` digest includes `image_extras`; if the dev row disappears (a flag round trip that deletes the row, a cleanup) or a column becomes NULL, prod's digest differs forever and the "upsert" never removes the row: the image is "changed" on every run (and counts toward the 25 % rule). Replace `image_extras` per image (delete then insert) for changed images, or compare and delete explicitly; make the registry mode `replace_per_image` for it (the upsert exception exists only for `images`). Same check for `description_notes`: a note cleared on dev ("Clearing a note deletes its Note tags") must delete the prod note, its embedding and lemmas; the replace covers it only if the group is replaced whole.

### M2. Comics: cross-batch unique-key conflicts and unshipped FK targets (P2 "Comics", "Apply" step 4, `models.py` 163 to 210)
- The text says comics are replaced "inside the image's batch transaction" and also "global sets ... in their own transaction in registry level order". Pick one.
- Unique keys `uq_comics_name_key`, `uq_comics_built_image_id` and `uq_comics_panels_image_id` are *global across comics*. A panel moved from comics A to comics B (or a rename swap A<->B) touches two built images, which are in different 500-image batches. If B's batch commits first, inserting B's panel with `image_id` X violates `uq_comics_panels_image_id` while A still holds X on prod; ordering by `created_at, id` is deterministic, so the same batch fails on every rerun (deadlock by determinism). Resolve by handling comics as one unit in a dedicated phase after the image batches: first delete every changed or pruned comics (panels cascade), then insert all changed ones, in one transaction.
- `built_image_id` is not nullified (amendment) and `comics_panels.image_id` is "blanked when the source image is not shipped". "Shipped" must mean *present on prod after the batches*, not "promotable on dev": with the files gate skipping an image, or `--limit-images`, the FK `comics.built_image_id -> images` or `comics_panels.image_id -> images` fails at the end of the run. Defer a comics whose built image is not on prod (report it), blank a panel's `image_id` unless its image exists on prod.
- A built comics ships "with its built image": the built image is also an ordinary promotable image; its digest group `comics` is keyed by that image, so the "replace changed image whole" and "replace comics whole" are the same operation and must be one code path.

### M3. `bit_xor` of row hashes cancels identical rows (P2 step 2; S3 Q3)
Hashing without the surrogate id (design excludes "ids nothing references") means two identical rows contribute the same 64-bit value and XOR to zero. Two different multisets with equal count and every value paired (A,A,B,B versus C,C,D,D) compare equal, so a change goes undetected. It needs duplicated rows (for example repeated identical tags with different ids) but the tables with excluded ids (`ocr_texts`, `tags`, `embeddings`) have no uniqueness on content. Cheap fix: also compute `sum(hashtextextended(...)::numeric)` (or `sum` of a second hash with another seed) and combine; or keep the `id` in the hash for those three tables, since the rows are copied with their ids anyway.

### M4. Statements about idempotency and "second run plans nothing" are too strong (P2 "Failure and resume", "Tests")
`statistics_snapshots` is recomputed on dev by every statistics-page view (`?live=true`) and hourly; `description_notes.lemmas_built_at/embedding_built_at` and `meme_profiles.updated_at` move whenever their jobs run; so a promote followed by a promote is rarely empty on a busy dev. Not a defect, but the integration test "idempotent second run" must say "with dev frozen", and the 25 % advice must use image counts net of those churn columns. Consider excluding the pure bookkeeping columns (`lemmas_built_at`, `embedding_built_at`, `extracted_at`) from the digest, with an explicit reason in `digest_exclude`; they carry no served meaning. Keep `updated_at` out only if no query orders by it (check `meme_profiles`).

### M5. Error text and tracebacks leak row values (P2 "Failure and resume", last bullet)
"Log lines carry table names, counts and ids only" holds for the tool's own log lines but not for exceptions. psycopg2 errors include the failing statement and `DETAIL: Key (name_key)=(...) already exists`, `COPY ocr_texts, line 3, column text: "..."`, and `UniqueViolation` for `comics.name_key` or `dedupe_key` names a comic or a filename. Wrap every database call: log `sqlstate`, `diag.constraint_name`, `diag.table_name`, never `str(exc)`, `diag.message_detail`, `diag.context` or the statement; install an excepthook that does the same; test it by forcing a violation with a recognisable sentinel string and asserting it is absent from captured output. The prod server log (statement + DETAIL at `log_min_error_statement=error`) also contains it: note it in P5/P7 as accepted (prod's own log).

### M6. A kept dev rating is lost when its image comes back (interplay P2 retire / P4 ratings rule 2 and 3)
Retire removes image X from prod (and its ratings); P4 keeps dev's rating rows for images absent from prod. The owner later un-flags X and promotes it; X exists on prod again with no ratings; the next pull's mirror rule 3 deletes dev's kept rating (prod has none). The "never lose the last copy" guarantee ends exactly when the image returns. Either have the pull keep dev rows whose `rated_at` is older than the last retire of that image (needs a tombstone list, for example `retired.json` history in the state directory), or decide that a returning image keeps its ratings by pushing them back (a class P write, so no), or document it as accepted. This is an owner decision (see questions).

### M7. Absorptions and the statistics snapshot carry non-promotable dev data to a public prod (registry `popularity_absorptions`, `statistics_snapshots`)
Absorptions hold `loser_filename`, `loser_content_hash` and winner/loser ids of images that are rejected, pending-aborted or flagged, which README says "never leave dev" for rows and files. The snapshot is computed from the whole dev corpus (pending, rejected, flagged included) and describes dev, not prod. Both are owner decisions already taken (1 and 4), but the privacy consequence (filenames and hashes of rejected content on a public database) is not stated. Options: ship only absorptions whose winner is promotable and blank `loser_filename` for losers that are not; compute the snapshot with `--promotable-only` before a promote (needs a P1 or batch change), or accept. Record the choice.

### M8. No completeness floor: an active image with no embedding or OCR is promoted (P2 "1. Plan" step 1)
`PROMOTABLE_IMAGES_SQL` is only status and flag. A just-promoted active image with no CLIP row is invisible to similar and visual search on prod, but visible in browse; one with no OCR is unsearchable. Ingestion promotion normally orders this, but `images` are also registered by direct script runs. Add a coverage report per table (counts of promotable images lacking rows) and a hard minimum: new images without an `embeddings` row (and without `ocr_texts` when OCR is configured) are skipped and listed, not shipped.

### M9. Guard numbers and edge cases at real sizes
- Retire threshold `max(50, 2 %)`: general 32,314 prod images gives 646; metal 18,087 gives 362; `it` 1,413 gives 50 (3.5 %, the floor dominates). Reasonable, but mass flags after a duplicate review round can exceed 2 % legitimately, so the flag will be used often, which is why it must be a counted flag (C2) and not a habit-forming boolean.
- "Promotable smaller than half of prod's" is vacuous for a first seed and for a prod with few images; "retire plus changed" are not combined: a plan with 1.9 % retire plus 24 % changed passes everything.
- `--limit-images` plus global sets plus retire: define the order (images, then globals, then retire last, only when the plan has no deferred images), otherwise a retire can run against a half-promoted set.
- `Zero images` guard should read prod's *count after* the plan, including when retire is the only action.

### M10. Tunnel target inconsistency with P5 (P2 "Connections"; P5 section on topology; S5 5.1)
P5 says Postgres is "on 127.0.0.1:5432 inside the compose network and not published", and the sync reaches it through `ssh -L`. An `ssh -L ...:127.0.0.1:5432` terminates on the VPS host's loopback, where nothing listens if the port is unpublished; the alternatives are publishing `127.0.0.1:55xx:5432` on the host (decision 7: three databases, one cluster, so one port) or targeting the container IP (changes on recreate). Also P3 installs a forced-command gate for `memesync`; a port forward needs `permitopen="127.0.0.1:<pgport>"` in `authorized_keys`, and a `restrict` option would forbid it. Decide whether the DB tunnel and the file gate use one key (then `permitopen` plus the forced command with `-N`) or two. This must be fixed in P5, noted here as an interface.

---

## Low

- **L1. Digest cost on a serving prod.** 1 GB read cold on a VPS disk, 4 workers each with its own connection, on a cluster shared by three databases whose `max_connections` is budgeted (decision 7). Default to 2 workers, set `statement_timeout`, and report the digest wall time. A dry run is not free for prod.
- **L2. `pg_advisory_lock` blocks.** Use `pg_try_advisory_lock` and exit 2 with "another promote holds the lock"; filter the `pg_stat_activity` look by `datname`, because the three databases share one cluster. (Advisory lock keys are per-database, so the "fixed key" does not collide across environments; worth saying.)
- **L3. `setval(max(id))` on an empty set.** `max` is NULL; use `setval(seq, coalesce(max(id), 1), max(id) IS NOT NULL)`. Applies to `concept_texts` and `concept_text_sets` on all three environments (empty today).
- **L4. Upsert of `images` by binary COPY.** COPY cannot upsert; staging requires a `TEMP` table and the privilege to create one (granted to PUBLIC by default; S5 does not say whether it is revoked). Specify how `images` (and the comics reorder) are loaded; the S3 measurement of staging cost used a regular staging table.
- **L5. Concept tables and their files.** `concept_image_sets.directory` and `concept_images.filename` refer to `CONCEPT_IMAGES_DIR` files that P3 does not ship. Confirm no prod route serves them (grep found only counts in statistics), otherwise the "row without file" rule applies to them as well.
- **L6. Exit code 4 and exit code 1 for batch failure** are used for different things in P3/P4; fine, but state that a partially applied run exits 1 with the list of committed batches.
- **L7. Role check is a snapshot.** Check `has_table_privilege` for INSERT, UPDATE, DELETE, TRUNCATE on class P and X tables, `pg_has_role(..., 'MEMBER')` for the backend and owner roles, `rolcreaterole/rolcreatedb/rolreplication/rolbypassrls`, and that the dev connection prefers `DATABASE_URL_READONLY` and *refuses* the write-capable URL (the repo already provisions the read-only role for all three environments; P2 reads only).
- **L8. Drift on prod is silently reverted.** A change made directly on prod in a D table (a stray admin write that the stage gate missed) shows up as "changed" and is overwritten without a word. Report "prod differs although dev did not change since the last recorded promote" as a separate line (needs the last promoted digests in the state directory, which H5 already records).

---

## Configuration and tunnel: recommendation

### Assessment of `PROD_SYNC_DATABASE_URL` plus a hand-opened tunnel

The owner's dislike is justified. Concretely:

1. **Wrong target.** Nothing ties the URL to the environment (C2). It is one shell variable among six (three environments, two roles, plus P3's `PROD_SSH_HOST`), and a variable set in one terminal survives into the next command.
2. **Secrets in history and process state.** `$env:PROD_SYNC_DATABASE_URL = "postgresql://sync_writer:...@127.0.0.1:55432/..."` is recorded by PSReadLine in `ConsoleHost_history.txt`; in Git Bash `export` lands in `.bash_history`. The password is also visible to every same-user process through the environment block, and it is inherited by every child process the tool spawns (including `ssh`).
3. **Forgotten or stale tunnel.** A tunnel opened by hand is either closed (confusing `connection refused` that looks like a prod outage), left open all day (a standing authenticated path from the workstation to prod), or pointing at a leftover port from a previous session on a different host. ExitOnForwardFailure, keepalives, host key policy and `BatchMode` are all choices the owner has to remember in a one-liner.
4. **Inconsistent knobs.** P2 uses `PROD_SYNC_DATABASE_URL`, P4 uses `PROD_PULL_DATABASE_URL`, P3 uses `PROD_SSH_HOST`, P6 uses both URLs, and none of them says which environment's database it is.
5. **Convention mismatch.** The repo's convention is a gitignored `environments/.env.<env>` loaded by `config.settings.load_env`, with tracked non-secret settings in `environments/settings*.yaml`. The sync tool currently invents a third mechanism.

### Proposal

**Files (no new mechanism, only new names that the existing `.gitignore` rule `.env.*` already covers):**

- `environments/.env.<env>.sync` (gitignored; one per environment, on the workstation only). Secrets and per-machine items only:
  ```
  SYNC_SSH_ALIAS=memes-vps               # a Host entry in ~/.ssh/config (HostName, User, IdentityFile live there)
  SYNC_WRITER_PASSWORD=...
  SYNC_READER_PASSWORD=...
  # optional: SYNC_SSH_BIN=C:\Windows\System32\OpenSSH\ssh.exe
  ```
- Tracked, non-secret expectations in `environments/settings.yaml` (group `sync`, per-environment values in `settings.<env>.yaml`): `sync.prod.database` (for example `memes_general`), `sync.prod.writer_role` (`sync_writer`), `sync.prod.reader_role` (`sync_reader`), `sync.prod.pg_host` and `sync.prod.pg_port` (the address of Postgres *as seen from the VPS*), `sync.prod.stage_marker` (the expected database comment). The point of the split: where to connect (secret file, per workstation) and what must be found there (tracked, reviewed) come from different places, so a mistake in one is caught by the other.
- One loader, `sync/config.py` (not part of the stdlib-only registry core): reads the `.sync` file with `dotenv_values` (never `load_dotenv`; nothing enters `os.environ` or the children), refuses a file that git tracks, warns when the file is readable by other Windows accounts (`icacls`), and validates required keys. No `--prod-url` option, no environment variable fallback. The tests never read `environments/.env.*.sync`; subagents and agents never get it (CLAUDE.md rule, extended: a `.sync` file is prod write credentials).

**The tool owns the tunnel** (`sync/tunnel.py`, a context manager used by promote, pull, files and verify):

1. Resolve ssh: `SYNC_SSH_BIN`, else `%SystemRoot%\System32\OpenSSH\ssh.exe` on Windows (P3 already decided against Git's ssh: different agent, different config handling), else `ssh` from PATH.
2. Pre-flight: `ssh -G <alias>`; refuse if the effective `hostname` is a loopback address or equals the dev machine, if `stricthostkeychecking` is `no`, or if the alias is missing. Print the effective user and host in the banner.
3. Choose a local port: bind `127.0.0.1:0`, read the port, close, pass it to ssh. The small race is closed by `ExitOnForwardFailure=yes` and up to three retries. Always `127.0.0.1`, never `localhost`: CLAUDE.md already documents the 44 ms IPv6 loopback stall on large requests, and `::1` may also be a different listener.
4. Command: `ssh -N -T -o ExitOnForwardFailure=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ControlMaster=no -o ControlPath=none -L 127.0.0.1:<port>:<pg_host>:<pg_port> <alias>`. `BatchMode=yes` is required: without it a missing agent identity produces a password prompt the tool cannot answer and the run hangs. The documented prerequisite is `Start-Service ssh-agent` and `ssh-add` once per boot. Add `-C` only by an explicit `--compress`.
5. Start with `subprocess.Popen(..., stdin=DEVNULL, stdout=DEVNULL, stderr=<temp file>, creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)` so a Ctrl-C in the console reaches the tool (which rolls back) and not ssh first; read stderr from the file only for diagnostics, after filtering.
6. Wait up to 15 s for the port to accept a connection while `poll()` is `None`; if ssh exits first, report its exit status and the last stderr lines (host key mismatch, permission denied, port in use) and stop.
7. Tear down in `finally` and `atexit` (`terminate`, then `kill`). On Windows also assign ssh to a Job Object with `KILL_ON_JOB_CLOSE` (ctypes, a dozen lines) so a hard kill of Python does not leave an authenticated listener behind; otherwise, print the pid at start.
8. Mid-run death of the tunnel surfaces as a connection error; the existing batch-level rollback and resume handle it. `ServerAliveCountMax=3` turns a silent drop into an error in under a minute.

**Connection and identity checks** (after the tunnel is up, before anything else; shared by every command in a `sync.connect` module):

- psycopg2 keyword arguments, not a URL string: `host=127.0.0.1 port=<port> dbname=<sync.prod.database> user=<role by command> password=... connect_timeout=10 application_name=memes-sync-<command> options='-c statement_timeout=...' target_session_attrs=read-write` (promote) or `read-only` mode (pull, verify). The role follows the command: promote and retire use `sync_writer`; pull, doctor and verify use `sync_reader`. Least privilege without any selection step by the owner.
- Check 1: `current_database()` equals `sync.prod.database`. Check 2: `current_user` equals the expected role. Check 3: `shobj_description(oid,'pg_database')` for the database equals `sync.prod.stage_marker` (for example `memes-sync:stage=prod;env=general`), which the P5 role script sets with `COMMENT ON DATABASE` as the owner (a catalog comment: no schema change, readable by any role). The marker's `state=seeding|serving` doubles as the "serving marker" of H7. Check 4: the dev connection's database comment is absent or says `stage=dev`, and dev and prod are different servers (postmaster start time, database oid, system identifier if readable). Check 5: for a non-empty prod, id overlap with dev (C2). Any failure is exit 2 with a one-line reason, before the first write.
- Banner printed on every command and repeated at the typed confirmation: `dev general (ocrdb @ localhost:5432, read-only)  ->  prod memes_general as sync_writer via ssh alias memes-vps (203.x.x.x)`; confirmation text is `general memes_general`.
- `python -m sync.doctor --env general` does the tunnel, both role logins, all five checks and the role-privilege check, writes nothing, and is the thing the owner runs after changing anything. A `--pin` is unnecessary because the tracked expectations are the pin.

**What changes in the existing designs.** P2 "Connections and credentials" is replaced by the above; the options table gets no connection options. P3 drops `PROD_SSH_HOST` for `SYNC_SSH_ALIAS` from the same file and reuses `sync/tunnel.py`'s ssh resolution and host-key policy for `ssh.exe`/`scp.exe`. P4 drops `PROD_PULL_DATABASE_URL`. P5's role script sets the database comment and prints nothing secret. P6/P7 runbooks lose the "open a tunnel by hand" step. Only three things remain for the owner: write one `.sync` file per environment, add one `Host` block to `~/.ssh/config`, load the key into the Windows ssh-agent.

Trade-off, stated honestly: the `.sync` file holds the sync passwords in clear text on the workstation. That is the same exposure class as `environments/.env.<env>` (which already holds the dev `DATABASE_URL`), it is outside the repo's tracking and out of shell history and the process environment, and it can be hardened later (Windows Credential Manager via `keyring`) without changing any interface. Key-based auth to Postgres would not help here because the connection arrives from the sshd side of the loopback and a `trust` or `cert` rule would then admit any local process on the VPS.

---

## Questions only the owner can answer

1. Ratings made on prod after the last pull: is a rating older than the pull window ever acceptable to lose? (Decides H1: the `SELECT` grant on `meme_text_ratings` for `sync_writer`, and how fresh the pull must be.)
2. When a retired image later returns (un-flagged, promoted again), should the ratings you gave on dev while it was away be kept, restored on prod, or dropped? (M6.)
3. Absorptions and the statistics snapshot carry filenames, hashes and counts of images that never ship (M7): accept, filter, or compute the snapshot on the promotable set only?
4. Is it acceptable that `sync_writer` can read (not write) a class P table, and that the prod database carries a `COMMENT ON DATABASE` marker set by the owner role? (C2/H1/H7; both are cheap and schema-free.)
5. Should a first seed be allowed to resume by the normal path (live indexes) when interrupted, or do you want to wipe and restart with the index-less path? (H7.)
6. Which statement describes your habits about flags: is `--allow-mass-retire` something you would type from habit? If yes, the counted form `--allow-mass-retire N` is the right default. (C2.)
7. May the sync passwords live in plain text in `environments/.env.<env>.sync` on the workstation, or do you want Windows Credential Manager from day one? (Configuration.)

Questions the implementer can settle but the design should state (not owner-only): how `images` and the reorder-sensitive comics are loaded under binary COPY (staging table or temp table, L4); whether the DB tunnel and the file gate share one key (M10); where the digest column lists come from (registry versus `information_schema`, H6); order of images, globals, retire under `--limit-images` (M9); whether `--only` composes with `--retire`; whether `--apply` re-plans or consumes the saved plan (recommend: re-plans and refuses if the plan hash differs from the one printed by the dry run it is told to follow, via `--plan-id`).

---

## What I tried to break and could not

- **Writing a class P or X table.** The data path is generated from registry D entries, `--only` is validated against them, and the role has no privilege on P/X tables. The two real paths are the `DELETE FROM images` cascade into `meme_text_ratings` (handled by design, with the hole in H1) and the cascades into empty X tables (harmless). A `TRUNCATE` is nowhere in the plan.
- **Deleting the wrong rows by the "replace changed image" order.** Deleting derived rows children-first and relying on `ON DELETE CASCADE` give the same result; nothing outside D references `image_descriptions`, `description_notes` or `meme_profiles` (checked against every `ForeignKey` in `Storage/models.py`). The `images` row is updated in place, so `meme_text_ratings` is not cascaded by a "changed" image.
- **`images` row against `batch_runs`.** `ingestion_batch_id` is nullified and `popularity_absorptions.batch_run_id` too; the FKs are `SET NULL`, so even a missed nullify would not fail, only leak a dangling id. Fine.
- **Absorptions CHECK and shape.** `ck_popularity_absorptions_shape` and the self-FKs allow the absorb, reverse, update sequence as designed (the problems are H4's update path and ordering, not the constraint set).
- **`setval`.** Integer ids, `concepts*` only; the S3 evidence and design are right apart from the empty-table edge (L3).
- **Comics deferrable position constraint.** `uq_comics_panels_position` is deferrable initially deferred, so a within-comics swap is safe; the cross-comics unique indexes are the real risk (M2).
- **Empty or tiny dev.** "No promotable images", "plan leaves zero images" and "half of prod" together do stop a blank dev; C1 and C2 are about the cases they do not cover.
- **Collation.** Text hashes are byte-based, so collation cannot cause false positives; only encoding and session formatting can (H6).
- **`json` and `vector` in the digest and in binary COPY.** `json` text is stored verbatim and round-trips; `vector` text is stable when the `vector` versions are equal (a precondition already). `jsonb` in `statistics_snapshots` is normalised.
- **Advisory lock across the three databases.** Advisory locks are scoped to a database, so one fixed key does not make different environments block each other.
- **Resume after a failed batch.** Finished batches are final and the digests recompute; that claim holds, given H5's per-batch snapshot and H6's stable digest.
- **Retire guard sizes at real counts.** 646 (general), 362 (metal), 50 (it) are sensible; the weakness is in how the guard is lifted, not in the numbers (M9).
