# R5a: Approve and reject lifecycle - design

Status: planned
Plan: docs/superpowers/plans/2026-10-29-r5a-approve-reject-lifecycle.md
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md (project R5, split into R5a and R5b)
Epic: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md
Follows: docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-28-r4-review-api-and-ui-design.md

R4 shows each `proposed` cut next to its source with the Approve and Reject buttons disabled. R5a makes them work: two endpoints that
decide a proposal, the state transitions behind them, flagging through the existing removal path, the popularity edge, the move of
text ratings, the `approved` and `rejected` metric events, and the enabled buttons with auto-advance. Keeping source/cut pairs out
of the duplicate jobs and auditing `ingest_hash_dedup` (OQ-7) is R5b.
Requirements covered: FR-9, FR-10, NFR-6 (the removal half of FR-11 and OQ-7 are R5b).

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | R5 is split: R5a is the lifecycle, R5b the duplicate exclusion and the batch audit | R5a is usable on its own; R5b is a smaller batch-side job with its own tests |
| 2 | A decision is one request and one transaction; it moves no file | Removal stays with `move_flagged` then `unregister_deleted_images` (D7, NFR-6). The source is only flagged after an explicit approve |
| 3 | The decision locks the `image_cuts` row (`FOR UPDATE`) and requires state `proposed` | Two tabs or a double click cannot decide twice; M1's `record()` already assumes this lock |
| 4 | One click or key, auto-advance, no undo | Reject only removes a derived cut. An approved source stays recoverable from the flagged page until `move_flagged` runs. Undo would need a `reverted` event and reversing the popularity edge and the rating move; it is a possible later project |
| 5 | Popularity goes through `guarded`, as at every other call site | A popularity problem must not block the decision. The edge has weight 0, so the cut ends with the source's popularity |
| 6 | `unregister_deleted_images` takes `cut_proposed` images only when they are flagged | A rejected cut must not leave a row pointing at a moved file, but an undecided proposal must survive an unplugged drive |
| 7 | The 409 body's `detail` is a reason code string | Existing 409s use a string `detail`; the page maps each code to a sentence |

## Backend

New `RecutDecisionService` in `Backend/app/services/recut_decision_service.py` (multi-step business logic, unlike R4's read side),
two methods added to `Backend/app/repositories/recut_repository.py`, two routes added to `Backend/app/api/recut.py`. Repositories do
not commit; `get_async_db` commits the request's transaction. No migration and no change to `Storage/models.py`: `state`,
`decided_at` and the foreign keys exist since R1.

### POST /api/recut/proposals/{id}/approve and /reject

No body. Success is 200 with `{ id, state, decidedAt }` (schema `recutdecision.schema.json`, generated for TypeScript, Kotlin and
Python). An `id` that is not a UUID is a 422; an unknown id is a 404 (`{"detail": "Proposal not found"}`).

Order inside the transaction: lock the `image_cuts` row, check the preconditions, apply the transition, record the event.

Preconditions, each a 409 whose `detail` is the code:

| Code | When | Applies to |
|---|---|---|
| `already_decided` | `state` is not `proposed` | both |
| `cut_missing` | `cut_image_id` is null, the image is gone, or its status is not `cut_proposed` | both |
| `source_missing` | `source_image_id` is null or the image is gone | approve |
| `source_not_active` | the source's status is not `active` | approve |
| `source_flagged` | the source is already flagged (something else is removing it) | approve |

**Approve** (FR-9):

1. The cut image's status goes `cut_proposed` to `active`.
2. The source is flagged with `ImageExtrasRepository.set_flagged(source_id, True, "recut_approved")`.
3. `PopularityRepository.absorb(source_id, cut_id, "recut", detect_run_id)` through `guarded`: a weight-0 edge, source loser, cut
   winner.
4. `meme_text_ratings` rows of the source move to the cut (`UPDATE ... SET image_id = cut`). The cut has none, so the unique
   `(query_hash, image_id)` constraint cannot collide.
5. `state = approved`, `decided_at = now()`.
6. `BusinessMetricsService.record("recut", "approved", detect_run_id, image_cuts.id)`, the same attribution `create_recut_cuts` uses
   for `proposed`.

**Reject** (FR-10): the cut is flagged with reason `recut_rejected`; `state = rejected`, `decided_at = now()`; the `rejected` event is
recorded the same way. The source is untouched.

Unflagging an approved source from the flagged page does not reverse the approval: `unmark_flagged` reverses only the
`cluster_review`, `agent_review` and `file_duplicates` edges, so the `recut` edge stays. That is deliberate (decision 4).

### Removal path

`move_flagged` moves every flagged image whatever its status and reads only `filename`, so a flagged `cut_proposed` cut (a rejected
one) is moved to `excluded/` unchanged. An approved source is a flagged `active` image and takes the unchanged path.

`unregister_deleted_images` (`batch/tasks/SourceTasks.py`, `UnregisterNonExisting`) today calls `get_all_images()`, which defaults to
`status = "active"`. R5a adds a second pass over `cut_proposed` images that are flagged, with the same missing-file and
is-a-directory checks. Unflagged `cut_proposed` images are never unregistered. Deleting the cut's `images` row cascades to its
derived data; `image_cuts.cut_image_id` becomes NULL (`SET NULL`), and the proposal row stays with state `rejected`, which R2 reads to
avoid proposing the same source again for the same detector version. Deleting an approved source sets
`image_cuts.source_image_id` to NULL and leaves state `approved`.

Known window until R5b: between an approve and the next `move_flagged`, the flagged source and its active cut can pair up in the
duplicate jobs.

## Frontend

- `MemesApi` gains `approveRecutProposal(id)` and `rejectRecutProposal(id)`, both returning the decision and throwing
  `CuratorRequestError(status)`; for a 409 the error also carries the `detail` code. `HttpMemesApi`, `mockApi` defaults and the
  generated types follow.
- `useRecutQueue` gains `decide(kind)` and `decisionStatus` (`idle | saving | failed | conflict`, with the conflict code). On success
  it removes the decided item from the loaded list and lowers `total` by one, so "n of total" stays right without a refetch.
- `RecutReviewPage`: Approve and Reject are real buttons; the keys `A` and `R` do the same, under the guard the arrow keys already
  have (Alt, Ctrl, Meta and Shift combinations, and INPUT, TEXTAREA and contenteditable targets, are ignored). The "arrives with the
  approve/reject step" hint is removed.
  - While a decision is in flight both buttons and both keys are disabled.
  - On success the page opens `nextId` of the decided proposal, else `previousId`; with neither it shows "No proposals waiting for
    review".
  - On a 409 it shows an alert with the sentence for the code, then reloads the queue (which drops a proposal someone else
    decided). Sentences: `already_decided` "Already decided"; `source_flagged` "The source image is flagged, so it can't be
    approved"; `source_missing`, `source_not_active` and `cut_missing` "This proposal can no longer be applied". No retry button.
  - On any other failure it shows "Could not save the decision" with the proposal still open; pressing the button again is safe
    because the server re-checks the preconditions.
- Not in R5a: confirmation, undo, bulk decide. The deferred R4 minors (M1, M3, M4) stay deferred; M5 disappears with the hint.

## Docs

- `backend_api.md`: the two endpoints in the "Recut review" section, with the 409 codes.
- `docs/image-status.md`: the `cut_proposed` rows now say the approve and reject endpoints exist; the R5 caveat on
  `unregister_deleted_images` is replaced by what it does.
- `docs/image-derived-data.md`: the `meme_text_ratings` row says it is implemented.
- `CLAUDE.md`: the `unregister_deleted_images` entry.
- Tracker: R5 row split into R5a (this spec) and R5b, with the R5b scope (duplicate exclusion for source/cut pairs, audit of
  `move_flagged` and `ingest_hash_dedup`, OQ-7).

## Testing

Backend (integration, test database):

- Repository: approve transition (statuses, flag with reason, state and `decided_at`), reject transition, ratings move, each
  precondition code.
- Service: popularity edge recorded on approve and a `guarded` failure does not block the decision; events recorded once with the
  `detect_run_id`; a second decision gets `already_decided`.
- Concurrency: two approves racing on one proposal, exactly one wins.
- `unregister_deleted_images`: a flagged `cut_proposed` image with a missing file is deleted and its proposal row survives as
  `rejected`; an unflagged `cut_proposed` image with a missing file is kept.
- Endpoints (`Backend/tests`): 200 shapes, 404, 422, each 409 code, and that neither endpoint touches the filesystem.

Frontend: buttons and the `A`/`R` keys, modified keys ignored, no double submit, auto-advance including the last item and the empty
queue, the 409 alert and reload, the generic failure keeping the proposal open, API client calls and error mapping.

## Not covered (known)

- Whether the overlay lines up in a real browser on a tall source image is still unchecked (R4); a visual check is needed once
  real proposals exist.
- Real proposals need R3a's migration and jobs to have run on a live database; R5a is verified against the test database only.
