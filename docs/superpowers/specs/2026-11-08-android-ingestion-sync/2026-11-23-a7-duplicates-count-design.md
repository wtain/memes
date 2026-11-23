# A7: Duplicates count on the Album sync screen (Android)

Status: planned
Plan: docs/superpowers/plans/2026-11-23-a7-duplicates-count.md
Originates from: owner's check of the general environment (2026-11-23): 12551 of 12552 uploaded on the phone, 12336 files in the inbox; the 215 difference could not be explained because the tracker does not record how an upload was answered.

## Intent

Show, next to "X of Y uploaded", how many of the uploaded files the server did not write because it already had them (answered `duplicate`), so the owner can reconcile the phone with the inbox: written = uploaded - duplicates.

## Goals

1. The tracker records, per uploaded file, whether the server answered `accepted` or `duplicate`.
2. The status card shows the cumulative duplicates count for the selected environment and album.
3. "X of Y uploaded" keeps its meaning: X includes the duplicates (owner's decision).

## Non-goals / anti-goals

- No retroactive classification. Rows uploaded before this change have no recorded answer and stay unclassified; re-sending 12000 photos to classify them is rejected.
- No list of the duplicate files (only the count); no change to the engine's behaviour, to what is uploaded or to the server.
- The run summary line ("N uploaded, M already on the server") stays as is (per run).

## Design

- Room: `TrackedFileEntity` gets `uploadAnswer: String?` (NULL = unclassified / not uploaded, `"accepted"`, `"duplicate"`). Database version 1 -> 2 with an explicit `Migration(1, 2)` (`ALTER TABLE tracked_files ADD COLUMN uploadAnswer TEXT`); no destructive fallback; schema JSON `2.json` exported and committed. `TrackerSql` gets constants for the two values (kept in sync with a test, like the state names).
- `markUploaded(environmentId, albumTreeUri, documentIds, answer, at)`: the engine groups the accepted and the duplicate positions of a response separately (they are already known in `MappedResponse`: `uploaded` includes `duplicates`) and writes each group with its answer. An already UPLOADED row is not changed (a late answer must not rewrite history), as today.
- `TrackerCounts` gains `duplicates: Int` (rows UPLOADED with `uploadAnswer = 'duplicate'`) and `unclassified: Int` (rows UPLOADED with `uploadAnswer IS NULL`), same environment scope as `uploaded`.
- Status card, under the headline and above the pending/rejected row:
  ```
  12551 of 12552 uploaded
  [progress bar]
  215 already on the server (duplicates)        <- shown when duplicates > 0
  12551 uploaded before this count existed      <- shown only while unclassified > 0
  1 pending   2 rejected
  ```
  Texts come from pure functions in `SyncMappers.kt`.
- Album change / reset wipes the environment's rows as today (counts follow).

## Approaches considered

1. **Column on `tracked_files` (chosen):** cumulative, survives restarts, one query, migration is a single ALTER.
2. Persist the per-run summaries in DataStore: cheap, but not per file, lost when a reset happens, cannot be shown cumulatively per album.
3. Ask the server for hashes: needs a new endpoint and phone-side hashing of 12000 photos; out of proportion.

## Left to the builder

Column name constants, string wording within the sketch, Compose layout details.

## Device check

After installing: the existing 12551 show as "12551 uploaded before this count existed". Run a sync with a few photos the server already has: the duplicates line appears and counts only those.
