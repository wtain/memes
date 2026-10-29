# P4: Manual popularity adjustment

Status: done
Plan: `docs/superpowers/plans/2026-10-29-p4-manual-adjustment.md`
Originates from: `docs/superpowers/specs/2026-10-26-meme-popularity/README.md` (epic; read it first), `2026-10-26-p1-data-model-and-absorption-rules-design.md` (the log and the cache this changes), `2026-10-27-p2-recording-winners-design.md` (E3 promised an "attach to a winner" action here; replaced, see G1) and `2026-10-28-p3-api-and-details-page-design.md` (the page this extends)
Follow-ups: P5 lists, sorting and top-popular (not written yet)

## Purpose

The calculated popularity can be wrong in ways the pipeline cannot see: a meme the owner knows is far more popular
(observed on social media) or whose count is inflated. The owner needs one correction: **set this meme's
popularity to N** (N = 1 is "reset"). P4 adds that action to the details page, recorded in the log so the number
stays explainable and `check_image_popularity` stays correct.

## Decisions made with the owner

| # | Decision |
|---|---|
| G1 | **The only correction is "set to N".** No per-entry reversal and no separate audit view for v1. The "attach a flagged image to a winner" action that P2 (E3) deferred to this design is dropped: the owner can fix a missing count by setting the number. |
| G2 | **An adjustment is a baseline shift, not a freeze.** Set to 40 and three identical copies arrive later: the meme reads 43. |

## Planner's calls (not stated by the owner; open to veto)

| # | Call |
|---|---|
| H1 | **Storage.** An adjustment is one more row of `popularity_absorptions`: `kind` absorb, new ninth source `adjustment`, no loser image, `winner_image_id` the meme being adjusted, `weight` the signed difference between N and the current number (may be negative). It is an ordinary open edge, so it travels with the meme if that meme is later absorbed (D5), and `check_image_popularity` needs no new concept. Needs a migration (for the owner to run): add `adjustment` to the source CHECK (and to `PopularitySource.ALL`, which `absorb` validates against) and replace the weight CHECK with `(source <> 'adjustment' AND weight >= 0) OR (source = 'adjustment' AND weight <> 0)`. It applies to every row kind, so a reverse row, which copies the edge's weight, stays valid. `reverse()` stays generic and may reverse an adjustment (reason `correction` already exists), but P4 adds no caller. The downgrade refuses with a clear error while any `adjustment` row exists, instead of deleting history. |
| H2 | **Set-to-N is naturally idempotent.** The repository computes `delta = N - current popularity` under the root's row lock; delta 0 writes nothing and returns `None`. A double click or a retry therefore adds no row. Repeated different values stack as separate rows, and the breakdown sums them into one line. |
| H3 | **Floor of 1.** A root's popularity is `max(1, 1 + sum of open edge weights in its tree)`, in the cache, in `PopularityRepository.popularity_of`, and in `verify`. Needed because a negative adjustment plus a later reversal could otherwise push a root to 0, which means "discarded". Because the floor is not linear, the repository stops moving the cache with increments: after any write (absorb, record, reverse, adjust) it recomputes the cache of the affected roots from the tree. This changes P1/P2 code; their tests must keep passing and gain a case with a negative adjustment. A reversal that releases a subtree sets the released loser to `max(1, 1 + its subtree)` the same way. **The sums stay raw and only the displayed root is floored:** `absorb` and `reverse` keep moving `weight + tree_weight`, so a negative adjustment inside a tree travels with it unfloored (a meme with two copies and a -4 adjustment has raw -1, shown 1; if it is absorbed, its winner loses 4 relative to the shown 1). This is a rare, accepted edge; the log stays linear and `verify` stays a pure function of the open edges. **The recompute helper floors only nodes with no open loser edge**; a node that has just become a loser is written exactly 0, never `max(1, ...)`. `adjust` diffs against the same floored derived value `popularity_of` returns, not the cached column, so a drifted cache heals on the next adjustment. |
| H4 | **Target is a discarded meme: 409.** A meme whose stored popularity is 0 has been absorbed; its count lives on the meme that absorbed it, so the endpoint refuses (409) and the page hides the control. Only `active` images can be adjusted (a flagged active image can; a `pending`, `rejected` or `cut_proposed` image answers 409 with its own message). 404 for an unknown or non-UUID id (as P3), and also when the image row disappears mid-operation (`WinnerGoneError`). |
| H5 | **Bounds.** N is an integer from 1 to 1,000,000; anything else is 422. |
| H6 | **API.** `PUT /api/images/meme/{id}/popularity` with body `{ "popularity": N }` (a new shared request schema) returns the same `MemePopularity` response as the P3 read, computed after the write. The popularity read (P3) and this write share the path. Errors surface to the caller: the E1 "never fail" rule applies to dedup paths only, not to an explicit owner action. |
| H7 | **Breakdown and labels.** The `adjustment` row shows as "Manual adjustment" with a signed count (+28, -4) and is listed last (after the sources ordered by count); the P3 schema's `count` minimum changes from 0 to allow negatives, then all three generators run. The breakdown sum (`own` plus the parts) equals the raw value, which is never above the shown `popularity`; when the floor applies the sum is lower, and the page shows both as stored. |
| H8 | **Page control.** In the P3 block, a "Set popularity" control: a small number input with the current value and a Save button, inline (no browser dialog, no confirmation step). On success the block shows the response. It is hidden for `discarded` images and shows an inline error message on failure. |

## Behaviour

**`PopularityRepository.adjust(image_id, target) -> PopularityAbsorption | None`.** Locks the image row (and
resolves its root: an image with an open loser edge raises `AlreadyDiscardedError`, which the service maps to 409),
reads the current number, writes the `adjustment` edge when the difference is not 0, recomputes the cache per H3, and
flushes. Never commits. It uses the same lock-and-retry loop as the other write operations (resolve the root, lock,
re-check the root), because the target can be absorbed between the check and the lock. The row's `reason`,
`dedupe_key`, `loser_filename` and `batch_run_id` stay empty; `winner_content_hash` is filled like the other edges.
Concurrent writes are last-write-wins: two tabs setting 40 and 50 leave +28 and +10 and a correct total.

**Floor change (H3).** One internal helper recomputes `images.popularity` for a given set of roots from
`1 + tree weight`, floored at 1; the four write operations call it instead of `_add_popularity`/`_zero_popularity`
arithmetic. `verify` compares the cache with the floored expectation.

**Page.** The block gains the control (H8) and the signed adjustment line; the label table gains `adjustment`.
Everything else on the P3 block is unchanged, including keyed refresh and silent failure of the read.

**Docs.** `backend_api.md` documents the PUT (body, 200/404/409/422). `CLAUDE.md`'s `check_image_popularity` entry
notes the floor and the adjustment source. `docs/schema.md` notes the new source and the signed weight. The epic
README lists this design and marks the original P4 scope (log view, entry reversal, attach) as replaced by G1.

## Tests

- Repository (integration, `ocrdb_test`): set 12 → 40 writes one `+28` edge and the cache reads 40; the same call
  again writes nothing; set to 1 on a root with absorbed copies reads 1; three more copies after setting 40 read 43
  (G2); a discarded image raises `AlreadyDiscardedError`; a negative adjustment followed by reversing an absorbed
  copy never takes the root below 1 and `verify` reports no drift (H3); an adjusted meme that is later absorbed
  transfers its adjustment with its tree; a reversal that releases a subtree with an adjustment leaves the released
  loser at `max(1, ...)`.
- Existing P1/P2 repository and property tests pass unchanged; add a negative-adjustment case to the property test.
- Migration test: new source accepted, negative weight accepted for `adjustment` only (on absorb and reverse rows),
  weight 0 rejected for `adjustment` but still accepted for `recut`, negative weight for any other source still
  rejected; downgrade refuses while an `adjustment` row exists and succeeds, restoring both CHECKs, when none does.
- Not adjustable: a `pending` image answers 409; a flagged active image can be adjusted and `verify` stays clean.
- API: 200 returns the new state; 404 unknown or malformed id; 409 for a discarded image; 422 for 0, negative,
  above the maximum and non-integer; the P3 read reflects the adjustment line.
- Frontend: the control submits N and the block shows the response; hidden for `discarded`; an error shows the
  inline message and keeps the old number; the signed line renders; `tsc -b`, `eslint src/ --max-warnings 0`,
  `vitest run`.
- Full roots, separately: `Backend/tests`, `tests/integration`, `tests/docs`.

## Goals

- The owner can correct any popularity to the number they believe, in one step, from the page they already use.
- The correction is in the log, explainable in the breakdown, and `check_image_popularity` keeps reporting no drift.
- Later duplicates keep counting on top of the correction.

## Non-goals

- No undo or editing of individual log entries, no log or history view (G1).
- No "attach to a winner" action and no change to how flags without `duplicateOf` count.
- No bulk adjustment, no note or reason field, no authentication or permission model beyond what the app has.
- No lists, sorting or top-popular (P5).

## Anti-goals

- A popularity of 0 on a meme that is not discarded.
- An adjustment that silently disappears when its meme is absorbed, or that is counted twice on a retry.
- An owner action that fails quietly (the dedup "never fail" policy must not leak into this endpoint).

## Constraints

- A migration for the owner to run (H1), after the P1 migration and in the current Alembic chain; recut work may
  have added revisions, so the builder takes `down_revision` from the current head.
- Changes the shared schemas (new request schema, relaxed `count` minimum): all three generators, no hand edits.
- Changes `backend_api.md`; frontend gate as in `CLAUDE.md`. Test database only (`ocrdb_test`), no live database,
  and the three environments' ports are occupied.
- Repositories do not commit.

## Left to the builder

- Names of the request schema, repository helper and component pieces; exact wording and layout of the control.
- How the recompute helper batches roots, and whether `popularity_of` shares it.
- The exact upper bound constant's home (settings or a module constant), if 1,000,000 needs to be configurable.
- The 409 message text.
