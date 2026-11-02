# OCR and distance details on the /duplicates review page

Status: done
Plan: `docs/superpowers/plans/2026-11-02-ocr-dedup-review-details.md`
Originates from: `docs/superpowers/specs/drafts/2026-08-19-ocr-assisted-deduplication-draft.md`
Follow-ups: none yet

## Purpose

The web `/duplicates` page shows each cluster as thumbnails with a "not duplicates" button and one OCR-lemma overlap score
per card. To tell a real repost from one template with different captions, the reviewer has to leave the page and use the
`/review-duplicates` agent flow or the CLI. This project puts the three missing facts on the page: the OCR text of each
member, the CLIP (or OCR-text) distance of each pair, and why each pair was paired. The page stays a display. The human
decides, as today (the principle set on 2026-09-04 in the draft). Detection and clustering do not change.

## Decisions made with the owner

| # | Decision |
|---|---|
| Q1 | **Pain to fix first:** the web page lacks OCR. The other draft items (huge templated clusters, an over-eager agent, missed duplicates) are not part of this project. Missed duplicates are largely covered by the shipped OCR-text probe. |
| Q2 | **Show:** OCR text per member, distance per pair, and why they were paired. Descriptions are not shown. |
| Q3 | **Scope: display only.** Nothing is written, no cluster splitting. Splitting would be its own project, after the owner has used this view. |
| Q4 | **Approach: extend the existing similarity endpoint** (`GET /api/images/duplicates/clusters/{id}/similarity`) rather than add a route or load every list item with pair data. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| N1 | **Pairs.** Each `ClusterSimilarityPair` keeps `image_id1`, `image_id2`, `overlap` and gains `candidates`: one entry per `tmp_duplicates` row of that pair, `{distance, source}` with `source` one of `clip`, `ocr_text`. A safety-net row is stored as `clip` and is not labelled separately. |
| N2 | **Every member pair is returned.** Union-find links members through others, so some pairs have no `tmp_duplicates` row. They are returned with `candidates: []` and the page says "linked through other members". |
| N3 | **Member OCR text** reuses the `text` the list items already carry (`_fill_texts_and_tags`) if the duplicates list fills it. If it does not, the similarity response gains a per-member text map. The plan checks this first and the answer is ledgered as a ruling. |
| N4 | **Cap.** `SIMILARITY_MEMBER_CAP = 100` stays. Over it the response has no pairs, and the page says pair details are unavailable for clusters over 100. Member OCR text is still shown. |
| N5 | **Panel.** A "Details" toggle per cluster, collapsed by default, at least 44 px tall. Opening it triggers the existing lazy fetch. Members: thumbnail, filename, full OCR text or "no text". Pairs: closest CLIP distance first (pairs with no CLIP candidate after those that have one), then OCR overlap as a percentage and one chip per candidate (`clip 0.031`, `ocr_text 0.082`). No filtering of the pair list. |
| N6 | **States.** Loading, error with retry, empty. Effects survive a StrictMode double mount and refs are touched only in handlers and effects. |
| N7 | **Read-only.** The endpoint only reads, keeps its no-cache headers, and logs no OCR text. No migration, no setting. |

## Behaviour

- The cluster grid, the best-overlap score on each card and the existing "not duplicates" and flag actions are unchanged.
- Opening Details on a cluster of 2 to 100 members shows the member rows and the pair list. A cluster over 100 shows the
  member rows and a notice.
- A pair stored once as `(LEAST, GREATEST)` is displayed regardless of the order in which the page lists the two members.
- The backend returns pairs for the cluster's active members (`get_cluster_member_ids` filters on `status = 'active'`, not on flagged). The panel shows only pairs between members the page still displays, so members already resolved on this page (kept-best flagged, dismissed) do not appear in the pair list.
- A pair has at most one stored candidate row (`uq_tmp_duplicates_pair` is on the two ids), so in practice one chip shows; when the CLIP probe and the OCR-text probe both match a pair, the first insert wins and the page shows that source only.

## Testing

Tests are written first and watched failing.

- **Repository and service** (real test database, rolled-back transactions): a pair with a CLIP row and an OCR-text row
  returns both candidates with their distances; a transitive pair returns `candidates: []`; a cluster over the cap returns
  no pairs; non-active members are excluded; the stored pair order does not change the output.
- **Endpoint** (mocked service): response shape, field names, no-cache headers.
- **Frontend (vitest):** the sort (closest CLIP distance first, no-CLIP pairs after); the `clip` and `ocr_text` chips; the
  "linked through other members" text; the over-cap notice; error with retry; collapsed by default and no fetch until
  opened; one fetch under a StrictMode double mount.
- **Whole roots:** `tests/integration`, `Backend/tests`, `tests/docs`, `batch/tests`, `tests/rules`, and frontend `tsc`,
  `eslint --max-warnings 0` and `vitest run`. The Android suite only receives the regenerated DTO.

## Contract and docs

- Edit `shared/schemas/clustersimilaritypair.schema.json` (new `candidates`, and the member text map if N3 needs it);
  regenerate the TypeScript, Kotlin and Python types.
- `backend_api.md` (the similarity endpoint).
- The draft `2026-08-19-ocr-assisted-deduplication-draft.md` gets a `Follow-ups:` link to this spec and a note that
  options (a) and (b) were superseded by the shipped OCR-text probe and lemma corroboration, and that splitting is not
  taken up here.

## Rollout

No migration. After the merge, restart a backend to serve the new fields.

## Out of scope

- Cluster splitting or grouping, and any bulk "not duplicates" action.
- Any change to detection, thresholds or `clusterize`.
- The `/review-duplicates` agent flow and its judgment.
- A separate label for the safety-net probe.
- Descriptions on the page, paging or filtering the pair list, raising the cap.
