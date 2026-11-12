# ADR 2026-11-12: dev/prod data sync technique (spike S3)

STATUS: PROPOSED (measured on throwaway containers; the owner confirms before P2 and P4 build on it)

Epic: `docs/superpowers/specs/2026-11-12-environment-data-sync/` (S1 classification, S2 sizes). This record answers S3.

## Context

Each environment has a dev instance (workstation, Windows, behind NAT, Postgres 18 + pgvector in Docker) and a prod instance
(Linux VPS). Dev initiates every sync over SSH. Class D (dev-origin) tables go dev to prod for active, unflagged images with the
image UUIDs preserved. Class P (prod-origin reader activity) is pulled prod to dev and may replace dev's copy. Prod serves
traffic while a promote runs. Real sizes (general, S2): about 32.3k promotable images; ocr_texts 1.05M rows, description_lemmas
2.4M, ocr_lemmas 619k, image_description_embeddings 43k x 1024d, meme_profile_situation_embeddings 31k x 1024d, embeddings 34k x
512d; about 950 MB of table data, more than half of the on-disk size being HNSW and GIN indexes.

## Decision (summary)

1. **Transfer: client-side `COPY ... TO STDOUT` from a filtered `SELECT` on dev, streamed as `FORMAT binary` into `COPY ... FROM
   STDIN` on prod, through an `ssh -L` tunnel, driven by Python (psycopg2).** New images are copied straight into the target
   tables; changed images are replaced (delete their derived rows, copy dev's rows) in one transaction per batch. A staging table
   plus `INSERT ... ON CONFLICT` is used only for tables that are replaced as a set or merged (see the per-table plan). Not
   `pg_dump` (cannot filter rows, about 3x slower end to end in the test), not logical replication (wrong direction, no parent-based
   filter, no dry run, WAL pinned on the workstation).
2. **Indexes stay in place on a serving prod.** Incremental insert into the live indexes is the default. Drop and rebuild is for a
   first seed of an empty, not yet serving prod (about 2.8x faster) or a maintenance window. Always run with
   `maintenance_work_mem` of at least 1 GB: at the real HNSW size, the 64 MB default is 5x slower (149 s against 30 s).
3. **Change detection: a per-image content digest** (`count` and `bit_xor(hashtextextended(row content))` per table, combined to one
   hash per image), computed on both sides and compared. Changed images get a **full replace of all their derived rows**. No
   schema change, no generation column. The `count(*) + max(created_at)` shortcut was tested and rejected (it misses most in-place edits).
4. **Retirement**: prod deletes images that are not in dev's promotable set with `DELETE FROM images` (the FK cascade removes the
   derived rows), behind a guard, **after pulling P tables**, because the same cascade also deletes `meme_text_ratings` on prod.
5. **Consistency**: files first, then rows; images row plus all its derived rows in one prod transaction per batch; retire rows
   first, delete files afterwards; one advisory lock per promote.

## Method and the hardware caveat

Two throwaway containers (`pgvector/pgvector:pg18-trixie`, Postgres 18.3, pgvector 0.8.2, pg_trgm 1.6, default config:
`shared_buffers` 128 MB, `max_wal_size` 1 GB, `synchronous_commit` on, `maintenance_work_mem` 64 MB,
`max_parallel_maintenance_workers` 2) on ports 55432 (sim-dev) and 55433 (sim-prod), schema applied with `alembic upgrade head`
(clean, head `a9d3e6b1c847`). **Both run on one workstation (24 logical CPUs, 31 GB RAM), loopback only: no network latency or
bandwidth limit is modelled, CPU and disk are shared by both sides, and the page cache is warm.** Absolute times are therefore
optimistic for the network and pessimistic for contention; ratios between techniques are the useful result.

Synthetic corpus: 15,000 images (9,553 promotable in the first 10,000 used as the "prod base"; 3% rejected/pending, 0.5% flagged).
Per-image row ratios taken from real general: ocr_texts 33, ocr_lemmas 24 (real 19), description_lemmas 82 (real 75), 1.33
descriptions and description embeddings, 1.0 CLIP embedding, 0.96 profiles with 1024d situation embeddings, 1.8 tags (real 2.2).
Vectors are uniform random (the hard case for HNSW build, the worst case for compression); lemmas are skewed pseudo-words from a
60k vocabulary (so GIN trigram costs are probably understated). Scale factor to real: general x3.38, metal x1.89, it x0.148
(promotable images 32,314 / 18,087 / 1,413 against 9,553).

## Q1 Transfer technique (class D, dev to prod)

Target tables had their secondary indexes dropped, so the numbers isolate the transfer (index cost is Q2). 9,553 images,
all 15 shipped tables, streamed through an OS pipe (true streaming, no spooling).

| Variant | Wall time | Bytes moved | Notes |
|---|---|---|---|
| (a1) direct `COPY` text pipe | 10.5 s | 465 MB | 44 MB/s end to end; dominated by Postgres on both sides |
| (a2) direct `COPY` binary pipe | 8.6 s | 227 MB | vector tables shrink 2.8x (1024d: 146 to 53 MB) |
| (a3) staging + upsert, empty target | 15.1 s | 465 MB | 4.2 s to staging, 10.8 s `INSERT ... ON CONFLICT` |
| (a3) staging + upsert, identical rows, `WHERE ... IS DISTINCT FROM` | 7.7 s | 465 MB | 3.4 s of statements (nothing rewritten) |
| (a3) staging + upsert, identical rows, plain `DO UPDATE` | 15.9 s | 465 MB | rewrites every row: bloat and index churn, avoid |
| (b) `pg_dump -Fc` of the unfiltered tables, piped to `pg_restore` | 33.2 s | 15k images, not 9.5k | includes non-promotable rows (4%) |
| (b) filtered: CTAS into schema `s3x` + `pg_dump -Fc -n s3x` | 2.6 + 16.8 s | 189 MB custom, 465 MB plain | plus 8.3 s `INSERT..SELECT` on prod = 27.7 s |
| (c) logical replication | n/a | n/a | see below |

Compression proxy (zlib on the stream; `ssh -C` is zlib): text 465 to 205 MB (level 1) / 189 MB (level 6, 25 MB/s CPU);
**binary 227 to 157 / 154 MB** (level 1 3.5 s, level 6 4.8 s). Text compresses 59%, but binary plus zlib (154 MB) beats text plus
zlib (189 MB). The vector tables do not compress (random floats; real embedding floats should behave similarly), the text-like
tables compress 50 to 65%.

**Extrapolation, first full seed of general (x3.38):** binary 768 MB, binary + zlib 521 MB, text 1.57 GB. Time on the wire:

| Uplink | binary raw | binary + `ssh -C` |
|---|---|---|
| 10 Mbit/s | 10.2 min | 6.9 min |
| 50 Mbit/s | 2.0 min | 1.4 min |
| 100 Mbit/s | 1.0 min | 0.7 min |

Database-side work for the same seed is about 36 s of load (no indexes) plus the Q2 index work; the wire dominates unless the
uplink exceeds about 100 Mbit/s. metal x0.56, it x0.044 of these figures. A routine promote moves only the changed images
(Q3), typically under 1% of that.

**(b) pg_dump.** Confirmed: `pg_dump` has no row filter (`--help` shows only object-level `--filter`, `--exclude-table-data`, no
`--where`). Workarounds: materialise the subset on dev (CTAS into a staging schema, measured 2.6 s) and dump that schema, then
`INSERT..SELECT` on prod, i.e. every byte is written twice on prod and the dump needs `pg_dump`/`pg_restore` client tools on the
Windows host or `docker exec`. 3.2x slower than (a2) (2.6x than a1) in this test and it cannot express "upsert" or "replace these images".

**(c) logical replication** (tested with a small schema, `wal_level=logical` on both): it needs prod to connect to dev, so the
workstation must be reachable from the VPS (a reverse tunnel `ssh -R` makes that technically possible). Measured findings that
rule it out here: (1) a row filter cannot use a subquery, so child tables cannot be filtered by the parent image's status
(`invalid publication WHERE expression`); (2) filtering `images` on `status` makes any `UPDATE` on dev fail ("Column used in the
publication WHERE expression is not part of the replica identity") unless `REPLICA IDENTITY FULL`; (3) the child row of a filtered-out
image still arrived on prod as an orphan (apply runs with FK triggers off); (4) with the subscriber offline, the slot pinned
227 MB of WAL after 200k small rows, so a workstation that is off or unreachable for days fills its own disk; (5) it replicates
every dev write immediately, which removes the "curate on dev, then roll out" review and any dry run. Not tested: a long-lived
reverse tunnel over a flaky link.

**SSH from Windows (not verifiable here; no `sshd` on this machine).** The command starts the tunnel itself:
`ssh -N -L 127.0.0.1:<local>:127.0.0.1:5432 -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3
[-C] <prod-host>` via `subprocess.Popen`, waits until the local port accepts connections, connects psycopg2 to it, and kills the
process in a `finally`. Windows OpenSSH has no `ControlMaster` multiplexing (`ssh -G` shows `controlmaster false`; the
platform limitation itself is from documentation, not tested), so one tunnel per run. The alternative `ssh <host> "psql ... -c 'COPY t FROM
STDIN (FORMAT binary)'" < stream` needs psql and a `.pgpass` on the VPS, and must never pass binary data through a PowerShell
pipeline (PowerShell 5.1 re-encodes it); from Python `subprocess` with binary pipes it is safe, and it is the fallback if direct
Postgres access through a tunnel is not allowed. `-C` pays off only below roughly 50 Mbit/s.

**Recommendation Q1:** (a2) binary COPY, `-C` optional by measured uplink, in explicit batches (Q5).

## Q2 Index strategy

Indexes on the shipped tables: 3 HNSW (1024d: image_description_embeddings, meme_profile_situation_embeddings, plus
description_note_embeddings; 512d: embeddings), 3 GIN trigram (ocr_lemmas, description_lemmas, description_note_lemmas) and
about 20 btrees. Base load 9,553 promotable images, 465 MB text COPY.

| Scenario | Load | Index build | Total | Table / index size after |
|---|---|---|---|---|
| (i) indexes present | 68.3 s | none | **68.3 s** | 289 / 356 MB |
| (ii-a) dropped, rebuilt, `mwm` 64 MB, 2 workers | 10.8 s | 14.0 s | **24.8 s** | 287 / 344 MB |
| (ii-b) dropped, rebuilt, `mwm` 1 GB, 7 workers | 10.5 s | 13.9 s | **24.5 s** | 287 / 344 MB |

Where the time goes at base size: with indexes, image_description_embeddings 25.4 s, meme_profile_situation_embeddings 17.4 s,
embeddings 10.5 s, description_lemmas 9.7 s, all other tables under 2.5 s. Rebuild per index: HNSW 1024d desc 6.0 s, HNSW 1024d profile
3.7 s, HNSW 512d 2.3 s, GIN description_lemmas 0.8 s, every other index under 0.5 s. **Three HNSW indexes are 85% of the cost.**

**Memory matters more than workers, and only at real size.** Standalone HNSW 1024d, 43,000 rows (real general), random vectors:

| `maintenance_work_mem` | workers | build time | index size |
|---|---|---|---|
| 64 MB (default) | 2 | **149.4 s** | 336 MB |
| 256 MB | 2 | 32.0 s | 336 MB |
| 1 GB | 2 | 30.5 s | 336 MB |
| 1 GB | 7 | 31.0 s | 336 MB |

(A 64 MB, 0-worker build was started and stopped after more than 3 minutes.) The graph has to fit in `maintenance_work_mem`
(the index is 336 MB); beyond that the build falls off a cliff. Seven workers gave no gain over two (inconclusive why: possibly the
parallel path is not engaged at these sizes, or memory bandwidth bound). GIN trigram on 1.23M `description_lemmas` rows: 1.3 s at
64 MB, 0.8 to 0.9 s at 1 GB (understated; see method).

**(iii) Incremental promote of k images into a populated, indexed prod (base 9,553 images, all indexes live) against drop + load +
rebuild of everything (`mwm` 1 GB):**

| Promoted | Images | Incremental | Drop + load + rebuild | Winner |
|---|---|---|---|---|
| 1% | 96 | 1.7 s | 14.4 s | incremental, 8x |
| 5% | 476 | 4.9 s | 17.0 s | incremental, 3.5x |
| 20% | 1,909 | 18.6 s | 20.6 s | tie |
| 50% | 4,774 | 46.8 s | 31.1 s | rebuild, 1.5x |

Incremental cost is about 9.8 ms per image (HNSW insert about 1.9 ms per 1024d vector at this size), rebuild is about 14 s fixed plus
3.7 ms per image, so **the crossover is about 25% of the table's images**. The rebuild constant grows with the table and so does the
incremental cost per row, so the fraction should hold at real size when `mwm` covers the graph; with the 64 MB default it shifts to
"never rebuild".

**Rule.** (1) Serving prod: never drop; insert incrementally at any percentage (50% costs only 1.5x). (2) First seed of an empty
prod, or any maintenance window: drop secondary indexes (not primary keys, not the unique constraints used by `ON CONFLICT`), load,
rebuild with `maintenance_work_mem` of at least 1 GB (1.5x the biggest index; 336 MB for 43k x 1024d) and `max_parallel_maintenance_workers`
of 4 or more. (3) The sync command sets `maintenance_work_mem` itself for its session and refuses to rebuild if the prod host
cannot grant it.

**Extrapolation to real sizes.** First seed of general with indexes live: 68 s x 3.4 = about 4 min of database time (the HNSW
inserts get slightly slower as the graph grows). With drop and rebuild: load 36 s plus index build about 70 s (HNSW 1024d 43k = 30 s
measured, profile 31k about 20 s, CLIP 512d about 13 s, GIN and btrees about 8 s) = about 2 min. Both are small next to the network. metal about half, it a few seconds.
Steady state, general: 1% (323 images) about 6 s, 5% (1,600) about 17 s, 20% (6,460) about 63 s of database time.

**Churn.** Replacing 5% of images in place (delete their derived rows, copy new ones, indexes live): delete 0.4 s, insert 2.6 s, table
+5.4%, index +2.2%; `VACUUM (ANALYZE)` 1.5 s; `REINDEX` of the 3 HNSW indexes 12.5 s afterwards. After deleting 100 images, `VACUUM`
took 10.4 s (HNSW vacuum is the slow part). Run `VACUUM` after each large replace/retire and a periodic `REINDEX CONCURRENTLY` of the
HNSW indexes if their size drifts.

**Is prod correct while an index is rebuilt?** Yes in results, no in speed. Measured on 11k rows: plain `CREATE INDEX` (GIN) lets readers
run (max 2 ms) but blocked an `INSERT` for 1.1 s (the build); `CREATE INDEX CONCURRENTLY` blocked nothing and took about 2x longer for GIN (3.0 s
against 1.6 s) and the same for HNSW (5.5 s against 5.6 s). **Plain `REINDEX` of an HNSW index blocked a reader for the whole build (5.1 s of 5.4 s;
ACCESS EXCLUSIVE)**; `REINDEX CONCURRENTLY` did not block (reader 2 ms). With the HNSW index absent, the top-10 cosine query is exact and
correct but 11x slower (21.7 ms against 1.9 ms at 11k rows; sequential scan, so at 43k rows about 85 ms and growing linearly), and
the backend's search code is unchanged. For a serving prod: build new indexes `CONCURRENTLY` under a new name, then drop the old one in the same
maintenance step; never plain `REINDEX`; never drop the HNSW index while serving unless a 10x slower, exact `/similar` is acceptable.

## Q3 Change detection for incremental promote

The derived tables mostly have no `updated_at`; only `created_at` (reset on a delete-and-insert rewrite, not on an in-place `UPDATE`),
`description_notes.updated_at` and `meme_profiles.updated_at` exist. The test corpus is 14,327 promotable images.

**(a) per-image digest.** One SQL statement per side: for every shipped table `count(*)` and `bit_xor(hashtextextended(content, 0))`
grouped by image (content = business columns, vectors as text; no surrogate ids that nothing references, no `created_at`),
combined into one hash per image. Descendants of `image_descriptions` join through it; `image_descriptions.id` is part of its own digest,
because the embedding, lemma and feedback rows hang on it.

| Digest variant | Time, 14,327 images, warm | Per image |
|---|---|---|
| full vectors (`embedding::text`), 1 worker | 1.8 s | 0.12 ms |
| full vectors, 4 parallel workers | 0.6 s | 0.04 ms |
| cheap vectors (first 8 dims + norm) | 0.9 s | 0.06 ms |
| prod-sim, 9,553 images, 4 workers | 0.5 s | 0.05 ms |
| `count + max(created_at)` fingerprint on 7 tables | 0.3 s | 0.02 ms |

Extrapolated to general (32.3k images, real data about 1 GB): about 4 s single worker, 1.5 s parallel on a warm cache; cold, it reads the
whole shipped data once, so on a VPS disk of 100 MB/s about 10 s. The manifest is 16 bytes plus a 38 byte UUID text per image, 1.7 MB for
general. Both sides compute digests, only the (id, hash) manifest crosses the tunnel. The baseline comparison of dev and prod-sim for identical
data found 0 differing images of 9,553.

**Detection test**: 500 images mutated on dev, 100 each by re-embedding (new vector and `created_at`), new description text, in-place
tag edit (no `created_at` change; only 21 of the 100 had an OCR tag to edit), owner remark, popularity change.

| Kind | Digest | `count + max(created_at)` |
|---|---|---|
| re-embed | 100/100 | 100/100 |
| new description | 100/100 | 100/100 |
| tag edited in place (21 images had the row) | 21/21 | 0 |
| `image_extras.remarks` | 100/100 | 0 |
| `images.popularity` | 100/100 | 0 |

No false positives (0 images changed that were not mutated). The shortcut misses every in-place change, so it is rejected as a primary mechanism
(it is an acceptable pre-filter only if all writers are known to rewrite rows, which is not true: remarks, popularity, feedback, `stale` flips).

**(b) full replace per image.** Used for every image the digest flags, and for new images (nothing to delete). For 500 changed
images (5%) with live indexes: 3.0 s total (see Q2 churn). Simple, correct for deletions inside an image (a removed tag, a shorter lemma list)
which an upsert would leave behind, and it needs no per-table logic. Cost is proportional to the changed images only.

**(c) generation / watermark.** The schema has no generation column and per-table `created_at`/`updated_at` is not reliable (above). It would need a
migration plus every writer's cooperation; not recommended. A `sync_state` table on prod holding the last promoted digest per image is optional and
is not needed because prod recomputes its own digest (it also detects drift on prod).

**Recommendation Q3:** (a) plus (b). An image regenerated on dev (new description, re-embed, retag, new remark) changes its digest on the next run and is replaced
whole in one transaction. Registry requirement for P1: the digest column list is part of the sync registry, with a test that fails when a model column
is neither in a digest nor explicitly excluded (otherwise a new column is silently never promoted). **Gap in the test:** `image_description_feedback` (in-place
`approved`) and the `stale` flag were not in the measured digest and must be added.

## Q4 Deletions and retirement

An image that is flagged, rejected, cut or deleted on dev drops out of the promotable set. Prod computes `retire = prod image ids - dev promotable ids` and deletes
them. Measured on a prod-sim clone with 100 planted P rows, 100 images deleted (1%, indexes live): 0.37 s, then `VACUUM` 10.4 s; 500 images (5%) 0.50 s.

**What `DELETE FROM images` cascades (all verified):** image_extras, ocr_texts, ocr_lemmas, embeddings, tags, image_descriptions (and through it
image_description_embeddings, description_lemmas, image_description_feedback), meme_profiles (and meme_profile_values, meme_profile_situation_embeddings),
description_notes (and description_note_embeddings), description_note_lemmas, plus the X tables with an image FK (tmp_duplicates, tmp_clusters, image_cuts is SET NULL),
and **meme_text_ratings: 100 rows to 0**. So the cascade is complete for every class D table with an image FK, and it destroys prod-origin rating data.

**Tables with no FK to images (from `Storage/models.py`; confirmed by the catalog):**

| Table | Class | After the image is deleted on prod | Handling |
|---|---|---|---|
| meme_saves, meme_impressions | P | rows stay (100 and 100 verified) | accept orphans (intentional: ids outlive images); the pull copies them |
| search_not_found, search_history, search_history_tags | P | no image reference | nothing to do |
| comics (`built_image_id`) | D | row stays, `built_image_id` set NULL (5 of 5 verified) | promote comics as a set restricted to built images that are promoted; delete the rest by name_key |
| comics_panels (`image_id`) | D | row stays, `image_id` set NULL; `source_image_id` is a snapshot, no FK | follows its comics; replace panels per comics |
| concepts, concept_image_sets, concept_images, concept_text_sets, concept_texts | D (provisional) | no image link | replace as a set, never merge |
| popularity_absorptions, image_cuts (SET NULL) | X | not shipped | n/a |

**Procedure:** (1) pull P tables first (P4 before P2 in every run; the command enforces the order or requires `--skip-pull`); (2) compute `retire`; (3) refuse when
`len(retire) > max(50, 2% of prod images)` unless `--allow-mass-retire` is given (an empty or wrong dev must not wipe prod); (4) `DELETE FROM images WHERE id = ANY(batch)` in
batches inside the batch transaction; (5) delete comics/panels as above; (6) `VACUUM` the HNSW tables; (7) only then does P3 delete the files.
**Rating hazard on dev as well:** a mirror pull (P4) replaces dev's `meme_text_ratings` with prod's, so a rating of a retired image would also vanish from dev after the next pull. P4 must
therefore mirror ratings only for images that exist on prod, and keep dev rows for images absent from prod (S1 tracker backlog item "ratings keyed by (query_hash, image_id)").

## Q5 Consistency while prod serves traffic

- **Atomicity.** One transaction (READ COMMITTED) per batch of 500 to 1,000 images writes the `images` row first (FK order) and then every derived table for those images. A reader on
  another connection saw 0 rows before the commit and the full image after (tested). Batches bound the transaction age (a long transaction holds back `VACUUM`) and make
  a failed run resumable: finished batches are final, the digest comparison picks up the rest on the next run. Never write derived rows in a separate transaction from their `images` row.
- **Locks.** Row locks only for inserts and deletes. Plain `CREATE INDEX` blocks writers of that table for the build (the sync is the only writer on D tables), plain `REINDEX` blocks readers; see Q2. Take
  `pg_advisory_lock(<fixed key>)` on prod so two promotes cannot interleave; take no table-level lock.
- **Order against the file sync (P3/S4 owns the files).** The contract: (1) copy files for new and changed images to prod under a temporary name and rename atomically; verify size/`content_hash`
  against the manifest; (2) commit the rows of exactly those images; (3) commit retirements; (4) delete files of retired images. A row without a file is a broken image
  on prod and prod does not run `unregister_deleted_images`; a file without a row is harmless garbage collected in step 4. A batch whose files are not all present is skipped and reported, not committed.
- **Search while a batch commits:** prod sees an image either not at all or complete; two images of a batch can appear at different instants, which is acceptable.

## Q6 Preconditions the command checks (read-only queries, run on both sides)

| Check | Verified in the sim | Rule |
|---|---|---|
| `alembic_version.version_num` equal | yes (`a9d3e6b1c847` both) | refuse otherwise |
| `server_version_num` major equal | yes (180003 both) | refuse otherwise (binary COPY and HNSW format) |
| `vector` and `pg_trgm` `extversion` equal | yes (0.8.2, 1.6) | refuse otherwise |
| vector dimensions from `atttypmod` on the shipped columns | yes (embeddings 512, description/profile/note embeddings 1024; ocr_text_embeddings 384 is X) | equal on both sides and equal to `EMBEDDING_DIM`, `TEXT_EMBEDDING_DIM`, `PROFILE_EMBEDDING_DIM` |
| `meme_profile_situation_embeddings` `(model_name, model_digest)` distinct values over the promotable set | yes, one value | exactly one pair; equals the pair prod's code/config expects |
| `meme_profiles.taxonomy_version`, `extractor_*` | yes, one value | taxonomy version equals the one in the deployed `taxonomy.yaml` |
| code revision | not in the DB | git SHA (or the YAML hashes of `taxonomy`, `settings*`) equal on both sides |
| Prod DB role | n/a | a dedicated sync role (S5), never the backend role |

**Gap:** the CLIP (`embeddings`), SBERT (`image_description_embeddings`) and description-note vectors carry **no model name or digest column**,
so their model parity cannot be checked in the database; the check can only be the code and settings revision. Not a schema change proposed here (charter says none); noted for P1.
**Sequences:** explicit ids do not advance a sequence (verified): after copying 7 into `concepts`, the next default insert failed with
`duplicate key ... concepts_pkey`; after `setval(pg_get_serial_sequence(...), max(id))` it worked. The sequence tables are `concepts`, `concept_image_sets`,
`concept_images`, `concept_text_sets`, `concept_texts` (integer, class D replace-as-set), `meme_impressions` (bigserial, P), `trend_sources`/`trends_run_results` (open), `tmp_clusters` (X).
The command runs `setval(max(id))` for each of them after copying; UUID-keyed tables need nothing.

## Q7 Reverse direction: pull prod to dev (class P)

Source: prod-sim with 100,000 rows each in `search_history` (+100,000 `search_history_tags`), `meme_impressions`, `meme_text_ratings` (all real indexes, target dev
with the images present). One REPEATABLE READ snapshot on prod for all four tables so the mirror is consistent across the FK-less links.

| Strategy | Time (400k rows) |
|---|---|
| `TRUNCATE` + `COPY`, target empty | 2.5 s |
| `TRUNCATE` + `COPY`, target already identical | 2.7 s |
| staging + upsert (`WHERE ... IS DISTINCT FROM`), identical | 1.9 s |
| staging + upsert + mirror delete (`NOT EXISTS` on staging), identical | 1.6 s |
| staging + upsert + mirror delete, source with 1,000 new rows, 200 deleted and 200 edited ratings | 1.3 s; ratings 99,800 (deletions mirrored) |
| watermark append (impressions `id >` max, searches `searched_at >` max), 1,000 new rows each | 0.02 s; **ratings stay 100,000 (deletions and edits invisible)** |
| `TRUNCATE` + `COPY` when dev lacks an image a rating references | **fails: FK violation on `meme_text_ratings_image_id_fkey`** |

At 100k rows every full strategy is under 3 s, so correctness decides. Full mirror scales linearly (about 6 us per row): 10M impressions would take about 4 minutes
and some 2 GB over the wire, the point at which the append-only impressions switch to the watermark (safe because they are never updated or deleted, S1) with a
periodic full mirror as reconciliation. Caveats found: (1) FK-less tables (`meme_saves`, `meme_impressions`, `search_not_found`) mean a mirror needs no FK order; (2) `meme_text_ratings`
needs staging plus an `image_id IN (SELECT id FROM images)` filter with a "skipped, image unknown on dev" count (the dev image may have been deleted before prod learned of it);
(3) `search_history` truncation must include `search_history_tags` (FK cascade; `TRUNCATE` without `CASCADE` refuses) and `meme_impressions` needs `setval(max(id))`
(done in the test); (4) a replace discards dev-made activity, as decided by the owner; (5) retention/deletion on prod (clear history, forget text) is only mirrored by the full
mirror or the anti-join delete, not by an append watermark.

## Per-table plan (input to P1 registry, P2 and P4)

Unless stated: copy FORMAT binary from the filtered select; new image = insert; digest differs = delete its rows then insert; retired = `DELETE FROM images`.
Filter for D: `images.status = 'active' AND NOT coalesce(image_extras.flagged, false)`.

| Table | Class | Method | Key / notes |
|---|---|---|---|
| images | D | insert new; update changed in place (never delete+insert while derived rows exist) | `ingestion_batch_id` NULL; popularity as a value; in digest |
| image_extras | D | replace per image | `remarks` only; in digest |
| ocr_texts | D | replace per image | largest row count; `bbox` is `json` (no equality operator; cast to text for compares) |
| ocr_lemmas, description_lemmas, description_note_lemmas | D | replace per image (via description for description_lemmas) | GIN trigram; composite PK |
| embeddings (512), image_description_embeddings (1024), description_note_embeddings (1024), meme_profile_situation_embeddings (1024) | D | replace per image | HNSW; precondition: dims, model name/digest |
| tags, image_descriptions | D | replace per image | `image_descriptions.id` must be preserved (children FK) |
| image_description_feedback | D | replace per image (via description) | not in the measured digest; add (`approved` is edited in place) |
| meme_profiles, meme_profile_values | D | replace per image | human-verified rows included |
| description_notes | D | replace per image | `updated_at` is a usable pre-filter here |
| comics, comics_panels | D | replace as a set, only comics whose `built_image_id` is promotable | no cascade (SET NULL); panels carry snapshots; not measured |
| concepts, concept_* (5 tables) | D (provisional) | replace as a set (truncate + copy in one transaction), then `setval(max(id))` | not measured; small (metal 25 MB) |
| meme_impressions | P | watermark append on `id`; periodic full mirror | bigserial; `setval` |
| meme_saves, search_not_found | P | staging + upsert + mirror delete | append-only, tiny |
| search_history, search_history_tags | P | staging + upsert + mirror delete (tags: replace per search) | UUID keys; message text is private data on both sides |
| meme_text_ratings | P | staging + upsert + mirror delete restricted to images that exist on both sides; skip unknown images and count them | cascade-deleted by image delete on prod: pull before promote |
| trend_sources, trends_run_results | open | undecided (S1 Open question 7) | n/a |
| class X, class R | X/R | never | n/a |

## Design outline

**P2 promote (dev to prod, dry-run first).** (1) preconditions (Q6); (2) pull P4 unless skipped; (3) `retire` and `new/changed` sets from the digest manifests
(Q3, Q4); (4) dry run prints counts per table, bytes, the retire list size and the guard decision, and stops; (5) real run: take advisory lock; file sync (P3) for the batch;
per batch one transaction `images` + derived tables; retire batches; `setval`s; `VACUUM (ANALYZE)` on the touched tables; (6) verify: per-table row counts and the digest of
the promoted images equal on both sides, report differences. Resumable by construction (digests are recomputed each run). A first seed uses the index-dropped path (Q2) and refuses on a
prod that is marked serving.

**P4 pull (prod to dev).** One REPEATABLE READ transaction on prod, FK-less tables first, ratings through the known-image filter, impressions by watermark with a periodic full mirror,
everything into dev in one transaction; dev's own activity is replaced; idempotent; a prod-only role with `SELECT` is enough.

**Safety properties to carry into P1.** The registry marks every table with its class; the dev-to-prod command can only name D tables and the pull only P tables (a unit test);
a guard on mass retire; dry run default; credentials only from environment, never in the repo.

## What was NOT verified

- A real SSH connection: no `sshd` here. Tunnel start-up, reconnect after a drop, `-C` throughput, Windows OpenSSH behaviour of `ExitOnForwardFailure`, and psql over `ssh host "..."` are described, not run.
- Any real network: no latency, bandwidth, or packet loss; the byte tables are arithmetic, not timing. Large COPY streams over a flaky link (resume at batch granularity is the mitigation, untested).
- Prod on a different Postgres minor/major, a different pgvector, a smaller VPS (RAM for a 1 GB `maintenance_work_mem`, disk speed, `shared_buffers`, autovacuum under load).
- Real data: uniform random vectors (HNSW recall and graph shape differ; build time is probably an upper bound), synthetic lemma vocabulary (GIN cost likely understated), real text compression ratio, real digest cost on cold cache.
- `HNSW` parallel build: 7 workers equalled 2 workers; reason not established.
- Query latency of the real backend while a batch commits or an index builds (only isolated top-10 queries and a lock test were run).
- comics, comics_panels, concepts, image_description_feedback, description_note_* beyond a few hundred rows: not exercised or only lightly (the plan for them is by analogy).
- Logical replication long-run behaviour (reverse tunnel stability); it was tested only for filters, direction and WAL retention.
- Binary COPY across different Postgres major versions (blocked by the precondition anyway).

## Reproduction

Scripts and raw outputs are not in the repo; they were kept in the S3 scratchpad of the session (`gen.py`, `lib.py`, `q1.py`, `q1b_*.py`, `q1c_logrep.sh`, `q1_binz.py`, `q2.py`, `q2_hnsw.py`,
`q3.py`, `q3b.py`, `q4_5_6.py`, `q7.py`, `*.out`). The containers `s3-sim-dev` and `s3-sim-prod` were removed.
