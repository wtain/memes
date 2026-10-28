# P3: API and meme details page

Status: draft
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; read it first), `2026-10-26-p1-data-model-and-absorption-rules-design.md` (the repository this reads) and `2026-10-27-p2-recording-winners-design.md`
Follow-ups: P4 corrections and audit, P5 lists and sorting (not written yet)

## Purpose

P1 stores popularity and P2 keeps it moving, but nothing shows it. P3 lets the owner see it: on a meme's details page,
the number and a breakdown by source. Nothing else (epic D4).

## Decisions made with the owner

| # | Decision |
|---|---|
| A1 | **Version 1 shows the number and the breakdown by source, nothing else.** No lists, no sorting, no audit trail, no correction controls. |
| A2 | **Flagged and discarded images are not special-cased.** The simplest rule applies: the response carries the number as stored (0 for a discarded image) plus a `status` of `normal`, `flagged` or `discarded`, and the page shows both. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| B1 | **A separate read endpoint**, `GET /api/images/meme/{id}/popularity`, not a field on the shared `Meme` schema. `Meme` is also the list item of search, duplicates and the Android client; a field there would put the number (and a query) on every list row, which is the later lists design. |
| B2 | **Status rule.** `discarded` when the stored popularity is 0 (the image is the loser of an open absorption edge), else `flagged` when the image is flagged, else `normal`, whatever the image's own status (pending and `cut_proposed` images read their stored number, normally 1; a rejected pending image that was absorbed reads 0 and is `discarded`). Discarded wins because a flag with `duplicateOf` produces both. A flag without `duplicateOf` leaves the image a root, so it reads `flagged` with its own number and breakdown. |
| B3 | **Breakdown rows.** `own` first (it is always 1), then the sources by count descending, ties by source name. Sources with weight 0 (recut) do not appear. A `discarded` image returns an empty breakdown, because `PopularityRepository.breakdown` is meant for roots only. The sum of the counts equals `popularity` for `normal` and `flagged` images while the cache is consistent with the log; if it has drifted, the response returns both as stored (the number from the cache, the rows from the open edges) and `check_image_popularity` is what reports it. No fallback and no recomputation in the endpoint. |
| B4 | **Labels live in the frontend**, keyed by source, with the raw key as the fallback so a source added later (P4's `manual`) shows without a release. Suggested wording: `own` "The image itself", `hash_in_batch` "Identical files dropped together", `hash_cross_corpus` "Identical files dropped again", `ingest_tier_a` "Ingestion review, close match", `ingest_tier_b` "Ingestion review, looser match", `cluster_review` "Duplicates page", `agent_review` "Duplicate review command", `file_duplicates` "Identical files found in the collection". The builder may improve the wording. |
| B5 | **Missing image.** An unknown id, or an id that is not a UUID, answers 404 with an explicit check in the new service method. `get_meme` has no 404 of its own, so this is a deliberate difference, documented in `backend_api.md`. |
| B6 | **The block never breaks the page.** While loading, and on any error (including a 404 from an older backend), the block renders nothing and logs nothing. It is a second request made by the details component, so a slow or failing popularity read cannot delay or hide the meme. |

## Behaviour

**Endpoint.** `GET /api/images/meme/{id}/popularity` returns
`{ "popularity": <int>, "status": "normal" | "flagged" | "discarded", "breakdown": [{ "source": <string>, "count": <int> }] }`.
It reads the cached `images.popularity` and the image's flagged state; for a non-discarded image it calls
`PopularityRepository.breakdown`. It writes nothing and commits nothing. It sends the same no-cache headers as
`get_meme`. Router to service to repository, per `CLAUDE.md`; the service builds the payload, the router has no SQL.

**Schema.** One new file in `shared/schemas/` (a response model plus the row type), then all three generators:
TypeScript types, Kotlin DTOs and Python models. A schema change with no diff under
`Backend/app/types/generated/` means the generator was skipped. Use the generated Python model as the router's
`response_model` (or, if hand-writing one, keep it identical: see the Known gotchas note on hand-written models
dropping fields).

**Details page.** `MemeDetails` (which already receives `flagged`) fetches the popularity for the shown image and
renders a small block: "Popularity" with the number, the status next to it when it is not `normal`, and one line per
breakdown row ("Identical files dropped together: 3"). A `discarded` image shows 0 and the status, and no rows. The
fetch is keyed by image id so navigating between memes refetches, and it survives a `StrictMode` double mount.
`MemesApi` gains `getMemePopularity(id)`, with the HTTP implementation and the mock in `src/test/mockApi.ts`.

**Docs.** `backend_api.md` documents the endpoint (parameters, response, statuses, 404). `CLAUDE.md` is unchanged
except where its popularity entries mention the details page. The epic README gets the `Follow-ups` link and this
spec's status moves with the work.

## Tests

- Backend (mocked DB) and integration tests on `ocrdb_test`: a root with absorbed copies returns the number and the
  rows in the B3 order, and the sum equals `popularity`; a fresh image returns 1 with `[own]`; a discarded image
  returns 0, `discarded` and no rows; a flagged root returns `flagged` with its number; a flagged image that is also
  discarded returns `discarded`; an unknown id returns 404; the endpoint writes nothing.
- Frontend: the block shows the number and rows for a root, shows 0 and the status for a discarded image, renders
  nothing while loading and on error, and an unknown source key shows its raw key.
- `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run`; the generated-type `git diff` check; the full Backend and
  `tests/integration` roots, run separately.

## Goals

- The owner can see any meme's popularity and where it came from, on the page they already use.
- The read is cheap and cannot affect the meme page or any write path.

## Non-goals

- No popularity on lists, no sorting, no top-popular page (later design).
- No audit trail of individual events and no correction controls (P4).
- No change to `Meme`, search responses or the Android UI.
- No caching layer; the stored number and one grouped query are enough.

## Anti-goals

- A number on the page that disagrees with the log (the response must come from the cache and the open edges, never
  a recomputation that differs from `check_image_popularity`).
- A popularity failure that hides or delays the meme itself.
- Per-row popularity queries creeping into list endpoints.

## Constraints

- Needs the P1 migration (`a4f7c1e92b68`) applied on the environment; an older database has no column, so the
  endpoint would fail there. The block's silent failure (B6) keeps the page usable meanwhile.
- Changes `backend_api.md`, `shared/schemas/`, three generated trees and the frontend: run the checks in
  `CLAUDE.md` ("Before committing backend/frontend changes").
- Test database only (`ocrdb_test`); the three environments' ports are occupied. No migration in P3.

## Left to the builder

- File and class names of the schema, the service method and the React component.
- Exact wording and layout of the block, within B4 and B6.
- Where the popularity read lives: the service may build `PopularityRepository(session)` (the global repository) directly or go through a small Backend repository method; the flagged state comes from the existing `get_is_flagged`.
- Android: only the Kotlin DTO is regenerated; no Android API interface or screen is wired.
