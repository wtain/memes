# S2: size measurement of the dev databases and disks

Status: done
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: `2026-11-12-s1-data-classification.md`

Measured 2026-11-12 on the three always-on instances (these are the future *dev* stages) with the read-only role
(`DATABASE_URL_READONLY`, SELECT only, exact `count(*)`, `pg_total_relation_size`). All three databases are at alembic head
`a9d3e6b1c847`. Disk figures are the real `BASE_PATH` directories. Nothing was written. Sizes are on-disk Postgres sizes, so they
include indexes and TOAST; table-only sizes approximate the logical transfer volume, because indexes are rebuilt on the receiving
side.

## Summary

| | general | metal | it |
|---|---|---|---|
| Database total | 2.4 GB | 1.1 GB | 59 MB |
| Active images | 32,329 | 18,088 | 1,422 |
| Promotable (active, unflagged) | 32,314 | 18,087 | 1,413 |
| Active and flagged (not promoted) | 15 | 1 | 9 |
| Rejected rows (not promoted) | 2,049 | 1 | 0 |
| Image files to promote | 3.3 GB | 1.9 GB | 121 MB |
| Class D tables, on-disk incl. indexes (approx.) | 2.0 GB | 0.95 GB | 51 MB |
| Class D tables, table-only (approx. transfer) | 0.95 GB | 0.5 GB | 21 MB |
| Class X tables not shipped (approx.) | 320 MB | 140 MB | 8 MB |
| Open: trends tables | 28 MB | 24 MB | 0 |
| Estimated prod disk (D files + D database) | ~5.3 GB | ~2.9 GB | ~0.2 GB |
| Dev-only files on disk, not shipped | ~470 MB | ~300 MB | 0 |

The prod disk is dominated by image files (62 % on general, 66 % on metal). The estimate leaves out WAL, backups and growth of the
reader-activity tables. The class-X saving is real but modest: `tmp_duplicates` alone is 278 MB on general (12 % of its database)
and 122 MB on metal. The trimming that matters most for the VPS is not shipping `rejected/`, `excluded/`, `possible_duplicates/`,
`inbox/` and `converted_originals/`, together about 470 MB on general.

## Biggest class D tables (general, on-disk total / table-only)

`image_description_embeddings` 569 / 231 MB, `meme_profile_situation_embeddings` 394 / 171 MB, `description_lemmas` 349 / 144 MB,
`ocr_texts` 288 / 189 MB, `embeddings` 195 / 97 MB, `ocr_lemmas` 166 / 44 MB. More than half of the ship-able database on general is HNSW and GIN
index size, which a bulk load rebuilds on prod instead of transferring; S3 must weigh the rebuild time against a faster load with
indexes dropped.

## Findings

1. **Row and file counts agree.** On all three environments, no promotable row has a missing file (0 of 32,314 / 18,087 / 1,413).
   This is a clean baseline for the row/file consistency check (P3, P6).
2. **Undocumented directories under `BASE_PATH` on general**: `possible_duplicates/` (1,882 files, 187 MB), `moved/` (empty).
   On metal: `excluded2/` (1 file). All subdirectories are class X; the file sync must be an allow-list of top-level files
   belonging to promoted rows, never a directory copy.
3. **Dev instances already hold reader activity.** `general` has 5,153 `search_history` rows, 637 `meme_impressions` and 38
   `meme_text_ratings`; `metal` 511, 21 and 1. These were written by using the dev backend. This breaks the S1 rule "dev never
   writes a P table": the owner searches and rates on dev too, so a P row cannot be assumed to come from prod. See the
   new Open question 10 in S1. `search_history` and `meme_text_ratings` have natural keys (UUID, `(query_hash, image_id)`), so a
   union is safe; `meme_impressions` (bigint serial id) would collide. `meme_saves` is empty everywhere.
4. **Reader-activity volume is tiny today** (a few MB at most); the first prod pull is not a size problem. Impression retention
   (S1 Open question 5) is a hygiene question, not a space one, for now.
5. **`trends_run_results` has about 249,000 rows (general) and 240,000 (metal)**, 28 and 24 MB, and keeps growing: `trends_batch`
   runs on the dev instances today. It stays an open question (S1 Open question 7); it is not a size driver for prod.
6. **`it` has no description, tag, profile or concept data**, so class D is only OCR, lemmas, embeddings and images there.
   The registry must accept empty tables (already noted in S1).
7. **Concepts are small** (metal `concept_images` 25 MB, 4,411 rows; general 34 rows, `concept_texts` empty everywhere).
8. **`processing_errors`, `image_cuts`, `popularity_absorptions` (1 row on general), `comics*`, `description_notes`
   (7 on general) are near-empty**, so the curation tables are small now and sync cost is dominated by derived data.

## Consequences for the plan

- The VPS needs roughly 6 GB for general, 3 GB for metal and 0.3 GB for it, before backups and growth. A 20 to 40 GB VPS disk
  would hold all three with room; this is an estimate, not a measurement of prod.
- Promotion volume is about 1 GB of table data plus 3.3 GB of files on the first full seed of general; later promotes are
  incremental and small.
- Index rebuild on prod (HNSW over three vector tables) is the likely slowest step of a first seed; S3 should measure it.
- The class X list in S1 holds up. The one correction is the origin rule for P tables (finding 3).
