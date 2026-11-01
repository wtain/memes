# Meme popularity (epic)

Status: approved
Follow-ups: `2026-10-26-p1-data-model-and-absorption-rules-design.md`, `2026-10-27-p2-recording-winners-design.md`, `2026-10-28-p3-api-and-details-page-design.md` (done), `2026-10-29-p4-manual-adjustment-design.md` (done), `2026-10-29-p5-ranking-and-trending-design.md` (done), `2026-11-01-s1-search-impressions-design.md` (done), `2026-11-01-s4-discovery-report-design.md` (planned); the signals tracker below lists the future projects

## Intent

Some memes keep coming back: they arrive in the ingestion inbox again and again, and each time the dedup
pipeline discards the newcomer. Today that signal is thrown away. This epic keeps it as a number per meme, its
**popularity**: how many copies of this meme the collection has ever seen, counting the discarded ones.

The owner's uses, in order: **ranking** (top-popular, sort by popularity), **audit** (where does this 12 come from?)
and **correction** (a review decision was wrong, fix the number).

## Definition

- Every image has a popularity, minimum 1. Every image that exists when this ships starts at 1. There is no
  backfill of history (flagged and rejected images already gone are not counted).
- When two images are confirmed to be duplicates, one is kept (the **winner**) and the other is discarded (the
  **loser**). The winner's popularity increases by the loser's popularity at that moment. This is the union-find
  size rule.
- A byte-identical file dropped into the ingestion inbox counts as a confirmed duplicate, even though it never
  becomes a database row. Several identical files in one drop: the one kept starts at the number of files, not 1
  (equivalent to 1 plus one absorption per extra copy).
- A candidate pair is not a confirmed duplicate. Rows in `tmp_duplicates` and `tmp_clusters` change nothing.
  A pair recorded in `duplicate_decisions` ("not duplicates") never counts.
- A screenshot recut cut inherits its source's popularity when the cut is approved. That is a transfer: the source
  is flagged and removed on approve, so it is the loser and the cut the winner, with source `recut`. A rejected
  cut changes nothing.

## Decided

| # | Decision |
|---|---|
| D1 | **Event log plus cached number.** An append-only absorption log is the source of truth; `popularity` on the image is a cached value that can always be recomputed from the log. Corrections are new log entries that reverse old ones, never edits to the number. Chosen because the owner wants to audit and correct, not just rank. |
| D2 | **Each absorption records**: winner, loser (null when the loser was a re-dropped file with no row), source, time and weight (copies it adds itself). The loser's popularity at that moment is derivable from the tree, not stored. |
| D3 | **Sources** (the breakdown shown to the owner): `hash_in_batch` (identical copies in one drop), `hash_cross_corpus` (identical to an active image), `ingest_tier_a`, `ingest_tier_b`, `cluster_review` (`/duplicates` "keep best"), `agent_review` (`/review-duplicates`), `file_duplicates` (`detect_file_duplicates`), `recut`. |
| D4 | **Version 1 shows** the number plus a breakdown by source on the meme details page. Nothing else. Lists, sorting and top-popular are a later design. |
| D5 | **Winner removal moves popularity on.** When a winner is itself discarded later (rejected in review, flagged then deleted), its whole accumulated popularity transfers to the new winner. Popularity follows the surviving meme. |
| D6 | **Undo reverses.** Undoing a reject or an un-flagging reverses the absorption it caused. |

### Planner's calls (not stated by the owner; mark in review)

| # | Call |
|---|---|
| C1 | **The log is a forest of edges; amounts are derived, not stored.** An absorption is an edge from loser to winner. A root's popularity is 1 plus the weight of every open edge in its tree, so reversing an edge anywhere in a chain is automatically correct and never refused. Reversals are new log rows; the cached number is recomputable from open edges. Detail in P1. |
| C2 | **Confirmation point per path.** Hash dedup: when the file is moved to `duplicates/` (immediate, no later reversal). Ingestion Tier A/B: when the reviewer's reject is applied; `undo-reject` reverses it, and so does `ingest_abort` for every absorption its rejected and pending images caused. `/duplicates` keep best and the agent flow: when the loser is flagged; un-flagging reverses it. Restoring a file from disk after `move_flagged` is outside the system and does not reverse. |
| C3 | **Winner in an ingestion cluster** is chosen server-side with no new UI or API input: the active-library member if the cluster has one, else the surviving pending member; among several candidates the one with the highest popularity, ties to the earliest `created_at`. Each rejected member is absorbed by exactly one winner. Design 2 may refine this. |
| C4 | **`detect_file_duplicates`** already keeps the oldest image by `created_at`; that image is the winner. Nothing about which images are removed changes. |
| C5 | **Idempotency.** Rows with a loser id are unique per (winner, loser, source) among unreversed entries. Re-dropped files have no row: each file moved to `duplicates/` is one absorption, recorded in the same transaction as the move, keyed by the ingestion batch, content hash and file name; the same file dropped in a later batch is another copy and counts again. |
| C6 | **Recut.** At approve (recut R5) an edge of weight 0 joins the source (loser) to the cut (winner), so the cut ends with exactly the source's popularity at that moment, including anything gained since the cut was created; a cut is created at 1. Design 2 specifies the hook; the code lands when R5 exists. |
| C7 | **Storage.** Cached `popularity` is a `NOT NULL` integer column on `images` with default 1 (existing rows become 1 with no backfill step; an older backend keeps working against the migrated schema). Updates are atomic increments or moves inside the transaction that confirms the duplicate. The log survives image deletion: it stores plain ids plus descriptors (file name, content hash), no foreign keys to `images`, so audit covers deleted losers and deleted winners. Design 1 settles the exact schema and the `image-derived-data.md` classification. |

## Goals

- Popularity is maintained automatically by every path that confirms a duplicate.
- Any popularity number can be explained from the log, and any wrong entry can be reversed.
- No existing pipeline behaviour changes: what gets rejected, flagged or moved stays exactly as today.

## Non-goals

- No change to duplicate detection (probes, thresholds, clustering).
- No backfill of historical duplicates. Everything starts at 1.
- No use of popularity in search ranking (separate decision, after the number exists).
- Version 1 has no lists, no sorting, no top-popular page, no correction UI.

## Anti-goals

- Counting a candidate pair as a duplicate before a human or an exact-hash match confirms it.
- A number that cannot be explained or recomputed.
- Counting the same duplicate twice (retries, re-runs, ingestion re-join with the same files).

## Constraints from this repo

- Several confirmation paths record only "this one loses" today (ingestion reject, `/duplicates` keep best, the
  agent flow, `detect_file_duplicates`); the winner is implicit. Recording the winner in each is most of the work.
  Hash dedup already finds the matching active image by `content_hash`, so its winner is known when it moves the file.
- Flagged images stay in clusters until `move_flagged` and `unregister_deleted_images` delete them. The transfer
  must happen when the loser is confirmed, not when it is deleted.
- Ingestion Tier A/B review has no winner among a cluster's members except "not rejected"; active-library images
  can never be rejected there.
- New tables with a foreign key to `images` must be classified in `docs/image-derived-data.md`
  (`tests/docs/test_image_derived_data_inventory.py` fails otherwise). The recut epic copies derived data to cuts
  using that inventory, so the classification interacts with it.
- Per `CLAUDE.md`: schema changes need an Alembic migration, API changes need `backend_api.md`, shared response
  types come from `shared/schemas/` and the three generators. Migrations and anything on the live metal, general
  and IT environments are run by the owner, not by the implementer.
- Related, not required: the recut epic's business-metrics event table (M1/M2) is also append-only; the builder may
  reuse its approach but this epic must not depend on it.

## Designs in this epic

Written one at a time, in this order. Each gets its own spec, plan and review.

1. **Data model and absorption rules**: the log table, the cached column, the transfer and reversal rules,
   idempotency, the `image-derived-data.md` classification. Migration for the owner to run.
2. **Recording winners in each path**: hash dedup (in-batch and cross-corpus), ingestion Tier A/B resolve,
   `/duplicates` keep best, agent flow, `detect_file_duplicates`, recut approve.
3. **API and meme details page**: popularity and breakdown by source on the image response, shared schema,
   generated types, `backend_api.md`, the details page.
4. **Manual adjustment** (replaces the original "corrections and audit" scope after the owner narrowed it): set a meme's popularity to a chosen number, recorded as a signed `adjustment` edge. No log view, no per-entry reversal, no "attach to a winner" action.
5. **Popularity in search ranking and a Trending memes page** (replaces the original "lists, sorting and top-popular" scope after the owner redirected it): a small capped boost to the text-search score, and an all-time trending page. See `2026-10-29-p5-ranking-and-trending-design.md`.
6. **Trending memes, last 30 days (S3)**: a switch on the Trending page between all time and the duplicates picked up in the last 30 days. See `2026-10-30-s3-trending-window-design.md`.

## Signals tracker (future projects)

Ideas raised while designing the epic, kept here so they are not lost. None is designed; each needs its own
brainstorm. The owner's observation behind them: a meme that keeps coming back through ingestion is either popular,
hard to find in this app, or both (the owner googles a meme they cannot find and re-drops it).

| # | Signal / project | Why it matters | State |
|---|---|---|---|
| S1 | **Search-result appearance frequency**: how often a meme is shown in results | A meme that appears often but is never saved is a ranking problem; one that never appears is invisible | done: `2026-11-01-s1-search-impressions-design.md` |
| S2 | **Save frequency**: how often a meme is saved with "save" in the app | The strongest "this was useful" signal the app can see | done: `2026-10-30-s2-save-count-design.md` |
| S3 | **Trending time window** (for example the last 30 days) | Separates "was popular once" from "is being re-dropped now"; the absorption log already has `created_at`, so no remodelling | done: `2026-10-30-s3-trending-window-design.md` |
| S4 | **Discovery: why the owner googles instead of finding the meme here**, including capturing failed searches | Re-ingestion mixes "popular" and "hard to find"; failed searches would separate them | planned: `2026-11-01-s4-discovery-report-design.md`; a read-only report over existing data, no capture |
| S5 | **Evaluating the ranking boost**: does it help the owner find memes | P5 ships a conservative default without measurement | follow-up once S1/S2 exist |
| S6 | **Failed-search capture in the clients**: Android treats a search that does not end with a save (the download action) as a miss, no extra tap; the web gets an explicit "didn't find it" button | The direct signal for "hard to find"; S4 only infers it from existing data | needs its own brainstorm (what is a search session on the phone) |
| S7 | **Admin page for the discovery report** | Browsable, with thumbnails and links, built on `repository/discovery_report.py` | after the S4 report has been used on real data |

## Left to the builder

- Table and column names, the exact shape of the log and the breakdown payload.
- How to make recording idempotent across retries and re-runs.
- Whether the log lives with the current `images` rows or survives their deletion (the loser's id may vanish);
  keep enough that the audit still reads.
- Which designs can run in parallel once design 1 is settled.

## Open for the owner

None at epic level. The planner's calls C1 to C7 above are open to the owner's veto.
