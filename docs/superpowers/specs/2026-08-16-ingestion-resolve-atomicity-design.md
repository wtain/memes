# Ingestion Review — `resolve()` Atomicity — Design

Status: done
Plan: docs/superpowers/plans/2026-08-16-ingestion-resolve-atomicity.md
Originates from: docs/superpowers/specs/2026-08-15-ingestion-submit-all-decisions-design.md

**Date:** 2026-08-16.

Makes `IngestionService.resolve()` apply each decision in a batch independently, so one bad
decision can no longer discard every other decision that already succeeded in the same request,
and so a successful DB write is never left describing a file that hasn't actually moved yet (or
vice versa).

---

## Motivation

`IngestionService.resolve()` (`Backend/app/services/ingestion_service.py:100-118`) loops over a
list of `{image_id, decision}` pairs. For each `reject`, it calls
`IngestionRepository.reject_image()` — which flushes (does not commit) `image.status =
"rejected"` — then immediately calls `image_store.move_to_rejected(filename)`, a real
`shutil.move` on disk. There is exactly one commit for the whole request, made by `get_async_db`
after the endpoint handler returns normally (per the repository-pattern convention in
`CLAUDE.md`: repositories/services never commit; `get_async_db` owns the single commit/rollback
boundary).

**Confirmed failure path:** `move_to_rejected()` is a real filesystem operation and can throw —
a locked file (antivirus scan, an open handle, common on Windows per this repo's own known
gotchas), a permissions error, a full disk. If it throws on the *k*-th decision in a batch, the
exception propagates out of the loop, out of the endpoint, and into `get_async_db`'s exception
path, which rolls back the *entire* session — undoing the flushed-but-uncommitted `status =
"rejected"` writes for all `k-1` decisions that already succeeded *and already had their files
physically moved*. Since the move already happened for those `k-1` images and is not itself
transactional, the result is `k-1` images whose file now lives in `rejected/` while the database
still shows their prior status (`pending`). `get_image_path()`
(`Backend/app/services/image_store.py:24`) is a flat `_IMAGES_DIR / filename` lookup that knows
nothing about the `rejected/` subfolder, so these images become unreachable through the normal
serving path. `undo_reject()` can't repair them either — it requires `status == "rejected"`
(`Backend/app/repositories/ingestion_repository.py:142-143`), which no longer matches after the
rollback. The only fix is manual filesystem surgery.

(The `else: raise HTTPException(422, ...)` branch for an unrecognized `decision` value, by
contrast, is **not** a realistic trigger — `Backend/app/api/ingestion.py`'s `Decision` Pydantic
model already restricts `decision` to `Literal["reject", "keep"]` at the request-parsing layer,
before `resolve()` ever sees it. That branch is unreachable through the actual API and is left
unchanged by this design.)

**Why this matters more now:** before the submit-all-decisions feature
(`docs/superpowers/specs/2026-08-15-ingestion-submit-all-decisions-design.md`), a batch was one
cluster's worth of decisions — a handful of images. "Submit all" batches every decided image
across every loaded cluster into one request, so the number of `move_to_rejected()` calls per
request — and therefore the odds of hitting one locked/busy file — scales with how much review
work a reviewer has queued up. A single bad file now risks discarding a much larger amount of
already-good work than before.

## Design

**Approach: per-decision commit, file move only after that decision's commit succeeds.**

For each decision, the DB write is committed *before* any filesystem side effect is attempted.
This changes the failure mode from destructive (file moved, DB reverted, image unreachable) to
self-healing (DB says `rejected`, file never moved because the commit-then-move ordering means a
move failure only follows an already-durable commit) — and critically, an error on decision *k*
no longer touches decisions `1..k-1`, because each of them is already its own committed
transaction, not a shared uncommitted one.

This is a deliberate, narrow, documented exception to `CLAUDE.md`'s "repositories must not call
`session.commit()` — `get_async_db` handles commit/rollback" rule, scoped to this one method. The
alternative (per-decision `SAVEPOINT` via `session.begin_nested()`, preserving the single
end-of-request commit) was considered and rejected: a `SAVEPOINT` isolates one decision's DB
error from poisoning the others, but does not make that decision *durable* before its file move
runs — a later, unrelated failure after the loop (or the process dying) could still roll back an
already-move-completed decision, reproducing the same orphaned-file bug this design exists to
close. Only a real commit closes that window.

### `IngestionRepository`: expose commit/rollback for this one caller

```python
# Backend/app/repositories/ingestion_repository.py

async def commit(self) -> None:
    """Commits the current transaction. Repositories otherwise never commit -- get_async_db
    owns that boundary -- but IngestionService.resolve() needs each decision durably applied
    before its associated file move runs, so a later decision's failure can't roll back an
    earlier decision whose file has already been physically moved. See
    docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md. Not for use
    outside resolve()."""
    await self.session.commit()

async def rollback(self) -> None:
    """Rolls back the current transaction -- paired with commit() above, same scope and same
    caveat."""
    await self.session.rollback()
```

### `IngestionService.resolve()`: per-decision try/except, commit-then-move, three result buckets

```python
async def resolve(self, tier: str, decisions: list[dict]) -> dict:
    """Apply per-image reject/keep decisions independently -- one decision's failure (DB or
    filesystem) does not affect any other decision in the same call. `decisions` is a list of
    {"image_id": UUID, "decision": "reject" | "keep"}. Partial resolution is expected --
    callers don't have to decide every member of a cluster in one call, and a partially
    successful batch is a normal outcome, not an error response."""
    rejected, kept, failed, move_failed = [], [], [], []
    for entry in decisions:
        image_id = entry["image_id"]
        decision = entry["decision"]
        try:
            if decision == "reject":
                filename = await self.repo.reject_image(image_id)
                if filename is None:
                    continue
                await self.repo.commit()
                try:
                    image_store.move_to_rejected(filename)
                except Exception as move_error:
                    # Broad on purpose: shutil.move can raise shutil.Error (not an OSError
                    # subclass) as well as OSError. Anything from this call must land here,
                    # not fall through to the outer handler below -- the DB commit already
                    # happened, so misclassifying this as `failed` would claim nothing was
                    # applied when the image is, in fact, durably rejected.
                    move_failed.append({"image_id": str(image_id), "error": str(move_error)})
                rejected.append(str(image_id))
            elif decision == "keep":
                await self.repo.mark_reviewed(image_id, tier)
                await self.repo.commit()
                kept.append(str(image_id))
            else:
                raise HTTPException(status_code=422, detail=f"Unknown decision: {decision!r}")
        except HTTPException:
            raise
        except Exception as e:
            await self.repo.rollback()
            failed.append({"image_id": str(image_id), "decision": decision, "error": str(e)})
    return {"rejected": rejected, "kept": kept, "failed": failed, "move_failed": move_failed}
```

Notes on this shape:
- **`failed`** covers DB-layer failures (the `reject_image`/`mark_reviewed` call or its commit
  raising) — that decision was *not* applied; nothing changed for that image, and it's safe to
  retry as-is.
- **`move_failed`** covers exactly the one case this design guarantees is safe despite failing:
  the DB commit for a reject succeeded (`image.status == "rejected"` is durable) but
  `move_to_rejected()` then threw. The image is correctly rejected in the database; its file
  simply never moved out of `_IMAGES_DIR`, which is not a broken state — `get_image_path()`
  still resolves it at its normal location (the move never got far enough to relocate it), and
  the existing `undo_reject()` endpoint already handles this cleanly for free: its
  `status == "rejected"` precondition now correctly matches, so calling it flips status back to
  `pending`; `move_from_rejected()` finds nothing in `rejected/` (since the move never
  completed) and no-ops, leaving the file exactly where it already was. No new repair endpoint
  or job is needed — this is a deliberate consequence of the design, not a gap.
- The `else: raise HTTPException` branch is left as-is (unreachable via the real API, see
  Motivation) and still aborts the whole request if somehow triggered — this is existing
  behavior for a path that isn't actually reachable, not a regression introduced here.
- `get_async_db`'s end-of-request commit still runs after `resolve()` returns normally; by then
  every decision has already been individually committed or rolled back, so that final commit
  is always a no-op for this endpoint. No conflict with the existing convention for any other
  endpoint — this exception is fully contained to `resolve()`.

### API contract change

`ResolveResponse` (`Backend/app/api/ingestion.py:60-62`) and the corresponding
`shared/schemas/ingestionresolveresponse.schema.json` both gain two new required array fields:

```python
class FailedDecision(BaseModel):
    image_id: str
    decision: str
    error: str

class MoveFailure(BaseModel):
    image_id: str
    error: str

class ResolveResponse(BaseModel):
    rejected: list[str]
    kept: list[str]
    failed: list[FailedDecision]
    move_failed: list[MoveFailure]
```

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ingestionresolveresponse.schema.json",
  "title": "IngestionResolveResponse",
  "type": "object",
  "properties": {
    "rejected": { "type": "array", "items": { "type": "string" } },
    "kept": { "type": "array", "items": { "type": "string" } },
    "failed": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "image_id": { "type": "string" },
          "decision": { "type": "string" },
          "error": { "type": "string" }
        },
        "required": ["image_id", "decision", "error"]
      }
    },
    "move_failed": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "image_id": { "type": "string" },
          "error": { "type": "string" }
        },
        "required": ["image_id", "error"]
      }
    }
  },
  "required": ["rejected", "kept", "failed", "move_failed"]
}
```

Run `Frontend/generate-types.sh` after the schema change and commit the regenerated
`Frontend/memes-frontend/src/types/generated/all.d.ts` (CI's type-diff gate will fail otherwise).
Android does not currently consume this endpoint (`AndroidClient/scripts/generate_dtos.py`
regeneration is not needed, but is harmless to run for consistency if touched later).

### Frontend impact (`IngestionReviewPage.tsx`)

Both `submitCluster` and `submitAll` currently clear `decisions` for every id that was *sent*
(`clusterDecisions`/`allPendingDecisions`), which was only correct because a partial failure used
to throw before the call could return successfully at all. Now that `resolve()` can return `200`
with some decisions in `failed`, clearing based on "what was sent" would silently discard
decisions that didn't actually apply, losing the reviewer's already-made judgment. Both functions
change to clear only the ids present in the *response's* `rejected`/`kept` arrays:

```typescript
const response = await memesApi.resolveIngestionCluster(tier, clusterDecisions)
setDecisions((prev) => {
  const next = { ...prev }
  for (const image_id of [...response.rejected, ...response.kept]) delete next[image_id]
  return next
})
if (response.failed.length > 0 || response.move_failed.length > 0) {
  setError(
    `${response.failed.length} decision(s) failed to apply and remain marked for retry` +
    (response.move_failed.length > 0
      ? `; ${response.move_failed.length} were recorded but their file move failed (safe to retry via undo/re-decide)`
      : "")
  )
}
load()
```

Failed decisions are left in `decisions` untouched, so they're naturally included in the reviewer's
next submit attempt without any special "retry" UI. This reuses the existing page-level `error`
banner rather than introducing a new surface, consistent with how the submit-all-decisions spec
handled errors.

### Non-goals

- No automatic retry of a failed move or a failed DB write — surfacing the failure and letting
  the existing submit flow retry it is sufficient; auto-retry adds complexity (backoff, retry
  limits) for a failure mode expected to be rare.
- No change to `undo_reject()` — it already does the right thing for the `move_failed` case, as
  shown above.
- Does not address stale/abandoned decisions resurfacing across a tier switch — that is
  `docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md`.

## Testing

**Backend (`Backend/tests/` — wherever `IngestionService.resolve()` is currently covered):**
- A batch of 3 reject decisions where the 2nd's `image_store.move_to_rejected` is mocked to
  raise: the 1st decision's DB status is `"rejected"` and stays that way (not rolled back); the
  2nd appears in `move_failed`, not `rejected`, and its status is still `"rejected"` (commit
  already happened); the 3rd still processes normally afterward (loop wasn't aborted).
- A batch where `reject_image`/`mark_reviewed` itself raises for one entry (mock the repository
  method): that entry appears in `failed` with an `error` message, its DB state is unchanged
  (rolled back to whatever it was), and subsequent entries in the same batch still process.
- A fully successful batch still returns `rejected`/`kept` populated and empty `failed`/
  `move_failed` lists (contract stays additive, not a breaking change in the success path).
- `undo_reject()` on an image left in the `move_failed` state correctly flips it back to
  `pending` and leaves the file where it already was (no-op move).

**Frontend (`IngestionReviewPage.test.tsx`):**
- `submitAll` response with a mix of `rejected`, `kept`, and `failed` entries: only the
  `rejected`/`kept` ids are cleared from `decisions`; `failed` ids remain and are included in a
  subsequent submit.
- A response with a non-empty `failed` or `move_failed` sets the page's error banner with a
  count-based message; the page does not treat this as a hard failure (clusters still reload via
  `load()`).
