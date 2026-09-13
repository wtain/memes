# Ingestion Review — Progress Visibility

status: approved
Originates from: a conversation reviewing why Tier B duplicate review "feels noisy" (2026-09-12/13) —
decomposed into four threads (review UX, text-heavy image classification, description-embedding-based
dedup, metrics/observability); this spec is the first slice of the metrics/observability thread
(operational visibility), chosen to ship first since the other three threads' premises need real numbers
to validate, not another round of impressions.

## Problem

`IngestionReviewPage`'s status banner shows `batch_runs.stats` verbatim — a JSONB blob written **once**,
at intake (`ingest_hash_dedup`/`ingest_validate_formats`: `intake`, `registered`, `converted`,
`hash_duplicates_in_batch`, etc.), and never recomputed as Tier A/B review progresses.
`IngestionService.get_run_status` returns `run.stats` unmodified (`Backend/app/services/ingestion_service.py`,
`get_run_status`); nothing between there and the frontend ever touches it again. A reviewer watching this
banner while working through Tier B review is, correctly, watching numbers that were never going to change
no matter how many decisions they submit — they're reading intake-stage history, not review progress.

Compounding this: even if the banner showed live numbers, `IngestionReviewPage` only re-fetches run status
(`getIngestionRunStatus()`) once on page load and again inside `load()`, which `runSubmit` only calls when
the on-screen queue fully empties (`finalCount === 0`, Ruling 3 — see `IngestionReviewPage.tsx:404-410`).
Every partial submit that leaves units visible — the common case, especially on Tier B's large
componenets — never refreshes the banner at all.

## Goal

Show real, live numbers during review: how many pending images still need *this tier's* review, and how
many are blocked from promotion batch-wide — refreshed after every submit, not just when the visible queue
empties — plus a session-relative delta ("since you opened this page", "from that submit") so a partial
submit's actual effect is visible without mental arithmetic against a remembered prior number.

## Non-goals

- Quality/precision metrics (does the CLIP-distance signal predict "is a duplicate" well) — a separate,
  harder follow-up thread; this spec is purely operational counts, derivable from data that already exists.
- A throughput/trend view (decisions per hour/day, historical charts) — approach 3 from the brainstorm,
  deliberately deferred; nothing here blocks building it later off the same `tier_*_reviewed_at` timestamps.
- Any change to `resolve`, `mark_reviewed`, `reject_image`, the tier bands, or the cursor — this is a new
  read-only count surfaced alongside the existing review flow, not a change to how review itself works.
- Removing the existing intake `stats` dict — it stays (still meaningful history), just no longer the only
  thing shown, and no longer positioned as if it were progress.

## Key facts this rests on

- `IngestionRunStatus`'s schema (`shared/schemas/ingestionrunstatus.schema.json`) has exactly
  `run_id, status, stage, stats, created_at, completed_at`, all required. `stage` is one of
  `hash_dedup | tier_a_review | ocr_prepass | tier_b_review | promoted`. `RunStatusResponse`
  (`Backend/app/api/ingestion.py`) mirrors it. `IngestionReviewPage.tsx`'s `tierForStage` maps
  `tier_a_review → "tier_a"`, `tier_b_review` and `promoted → "tier_b"`, everything else → `null`
  (`promoted` falls back to tier_b's now-empty queue rather than a dedicated "done" view — a documented
  safety net, not the normal path).
- `IngestionRepository.get_tier_candidate_rows(batch_id, tier, distance_low, distance_high)` — the
  existing predicate set both `list_clusters` (Tier A) and, via its own raw-SQL restatement,
  `list_tier_b_review_page` (Tier B) already build subject sets from: `{tier}_reviewed_at IS NULL`, band,
  neither side `rejected`, at least one side `pending` in this batch. The subject side is whichever of
  `image_id1`/`image_id2` is the batch's `pending` image (a `UNION ALL` of both directions in the Tier B
  raw-SQL form, an ORM `or_(...)` in Tier A's — same set, different query shape per tier's own method).
- `IngestionRepository.get_blocked_pending_ids(batch_id, tier_a_high, tier_b_high)` returns the union of
  both tiers' *rows'* `image_id1`/`image_id2` — including the non-pending (`active`) side of a
  `cross_corpus` match, since the row-level predicate only requires *one* side pending. That's harmless
  for its actual caller (`ingest_promote`, which only ever looks up pending ids in it), but makes
  `len(...)` an imprecise "how many pending images are blocked" count — reusing it here would
  quietly overcount. This spec's new count derives "blocked" from the same precise, pending-only subject
  sets it computes for the per-tier numbers instead (their union), not from this method.
- `_tier_band(tier)` (`Backend/app/services/ingestion_service.py`) already resolves each tier's
  `(low, high)` — `("tier_a", 0.0, PROXIMITY_THRESHOLD)`, `("tier_b", PROXIMITY_THRESHOLD,
  settings.DUPLICATES.THRESHOLD)`. Reused as-is; not re-derived.
- `db_session`/repository session rules are unaffected — this is read-only, no new writes anywhere.

## Design

### 1. Repository — `count_unreviewed_subjects`

New method on `IngestionRepository`, mirroring `get_tier_candidate_rows`'/`list_tier_b_review_page`'s
subject-set predicates but returning a count instead of materializing rows (no filenames, no OCR, no
candidate join — cheap by construction):

```python
async def count_unreviewed_subjects(self, batch_id, tier: str, distance_low: float, distance_high: float) -> int:
    """Count of distinct PENDING images in this batch with at least one still-open (not yet
    reviewed for this tier, other side not rejected) candidate pair in this tier's band -- the
    same subject set get_tier_candidate_rows/list_tier_b_review_page build, just
    COUNT(DISTINCT ...) instead of materializing rows. See
    docs/superpowers/specs/2026-09-13-ingestion-review-progress-visibility-design.md."""
    assert tier in ("tier_a", "tier_b"), f"unknown tier: {tier!r}"
    reviewed_col = "tier_a_reviewed_at" if tier == "tier_a" else "tier_b_reviewed_at"
    # reviewed_col is one of exactly two hardcoded column names (asserted above), never derived
    # from external input -- the f-string only ever selects between two fixed literals.
    sql = text(f"""
        SELECT count(DISTINCT subject_id) FROM (
            SELECT td.image_id1 AS subject_id
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id1
            JOIN images o ON o.id = td.image_id2
            WHERE td.{reviewed_col} IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
            UNION ALL
            SELECT td.image_id2 AS subject_id
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id2
            JOIN images o ON o.id = td.image_id1
            WHERE td.{reviewed_col} IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
        ) subjects
    """)
    return (await self.session.execute(
        sql, {"low": distance_low, "high": distance_high, "batch_id": batch_id})).scalar()
```

For "blocked total" (batch-wide, both tiers), the service calls this twice (once per tier's band) and
needs the *union's size*, not the sum (an image blocked in both tiers at once — possible, since Tier A and
Tier B bands are adjacent, not exclusive of the same image having open pairs in both — would double-count
a naive sum). Rather than a third query, add a sibling method returning the actual id set for the union
case:

```python
async def unreviewed_subject_ids(self, batch_id, tier: str, distance_low: float, distance_high: float) -> set:
    """Same subject set as count_unreviewed_subjects, as ids rather than a count -- used to union
    across tiers for a batch-wide blocked-total that can't just sum two per-tier counts (an image
    can be open in both tiers' bands at once)."""
    assert tier in ("tier_a", "tier_b"), f"unknown tier: {tier!r}"
    reviewed_col = "tier_a_reviewed_at" if tier == "tier_a" else "tier_b_reviewed_at"
    sql = text(f"""
        SELECT DISTINCT subject_id FROM (
            SELECT td.image_id1 AS subject_id
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id1
            JOIN images o ON o.id = td.image_id2
            WHERE td.{reviewed_col} IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
            UNION ALL
            SELECT td.image_id2 AS subject_id
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id2
            JOIN images o ON o.id = td.image_id1
            WHERE td.{reviewed_col} IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
        ) subjects
    """)
    rows = (await self.session.execute(
        sql, {"low": distance_low, "high": distance_high, "batch_id": batch_id})).all()
    return {r.subject_id for r in rows}
```

`count_unreviewed_subjects` stays (used for the cheap, single-tier "how many remain for the tier I'm
looking at" number — no need to fetch full id sets there); `unreviewed_subject_ids` is used only for the
batch-wide blocked total, where the actual ids are needed to union. Both share the identical query body
apart from the outer `SELECT count(...)` vs `SELECT DISTINCT ...` — accepted duplication (the two shapes
can't share one prepared statement without a driver-level cost to build one from the other that isn't
worth it for ~20 lines of SQL each).

### 2. Service — `get_run_status` computes the new fields

```python
def _tier_for_stage(stage: Optional[str]) -> Optional[str]:
    """Mirrors IngestionReviewPage.tsx's tierForStage exactly -- keep both in sync if either
    changes. 'promoted' falls back to tier_b's (by-then-empty) queue for the same reason the
    frontend does: once a run completes it drops out of get_run_status entirely, so this is a
    safety net, not the normal path."""
    if stage == "tier_a_review":
        return "tier_a"
    if stage in ("tier_b_review", "promoted"):
        return "tier_b"
    return None
```

```python
    async def get_run_status(self, batch_id: Optional[UUID] = None) -> dict:
        resolved_id = await self._resolve_batch_id(batch_id)
        run = await self.repo.get_run(resolved_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Ingestion run not found")

        tier_remaining = None
        blocked_total = None
        current_tier = _tier_for_stage(run.stage)
        if current_tier is not None:
            low, high = _tier_band(current_tier)
            tier_remaining = await self.repo.count_unreviewed_subjects(resolved_id, current_tier, low, high)
            tier_a_low, tier_a_high = _tier_band("tier_a")
            tier_b_low, tier_b_high = _tier_band("tier_b")
            blocked_ids = await self.repo.unreviewed_subject_ids(resolved_id, "tier_a", tier_a_low, tier_a_high)
            blocked_ids |= await self.repo.unreviewed_subject_ids(resolved_id, "tier_b", tier_b_low, tier_b_high)
            blocked_total = len(blocked_ids)

        return {
            "run_id": str(run.run_id),
            "status": run.status,
            "stage": run.stage,
            "stats": run.stats,
            "tier_remaining": tier_remaining,
            "blocked_total": blocked_total,
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }
```

Both new fields are `None` outside `tier_a_review`/`tier_b_review`/`promoted` (`hash_dedup`, `ocr_prepass`
have no candidate pairs yet to count — `None` distinguishes "not applicable at this stage" from "zero
remaining", which the frontend should treat differently: "0 remaining" during active review is a real,
meaningful signal — the queue is drained; `None` before a tier has started isn't).

### 3. Schema + generated types

`shared/schemas/ingestionrunstatus.schema.json` gains two properties, both nullable, both required (present
with an explicit `null` rather than omitted — consistent with `stage`/`stats`/`completed_at`'s existing
nullable-but-required pattern):

```json
    "tier_remaining": { "type": ["integer", "null"], "description": "Pending images still needing review in the CURRENT stage's tier; null when the stage has no active tier (hash_dedup, ocr_prepass)." },
    "blocked_total": { "type": ["integer", "null"], "description": "Pending images in this batch with an unresolved candidate pair in either tier; null under the same condition as tier_remaining." }
```

added to `required` alongside the existing five. Regenerate all THREE generator trees per this repo's
established rule (the stopgap and an earlier Tier B branch both forgot the Python DTO tree — see
`documents/generation.md`): TypeScript (`Frontend/generate-types.sh`), Kotlin
(`AndroidClient/scripts/generate_dtos.py`), and Python (`datamodel-codegen`, `Backend/app/types/generated/`)
— commit all three, verify no drift.

`RunStatusResponse` (`Backend/app/api/ingestion.py`) gains the matching two fields:
```python
    tier_remaining: Optional[int]
    blocked_total: Optional[int]
```

### 4. Frontend — refetch progress after every submit, not just when the queue empties

`IngestionReviewPage.tsx`'s `runSubmit`, the branch at `:404-410` (`if (finalCount === 0) { await load()
}`) becomes:

```tsx
      if (finalCount === 0) {
        await load()
      } else {
        // The visible queue didn't empty, so a full reload isn't warranted -- but the progress
        // numbers (tier_remaining/blocked_total) did change. Refresh just the status, not the
        // whole page, so the banner reflects this submit instead of going stale until the queue
        // happens to empty (Ruling 3's original gap -- see this spec's Problem section).
        try {
          const freshStatus = await memesApi.getIngestionRunStatus()
          setStatus(freshStatus)
        } catch {
          // Best-effort -- a failed progress refresh shouldn't surface as a page error or block
          // a submit that already succeeded.
        }
      }
```

No new API client method — `getIngestionRunStatus()` already exists and now returns the richer object on
the same endpoint.

### 5. Frontend — session-relative deltas

Two new pieces of state, both derived from `status.tier_remaining`/`status.blocked_total` as they change,
no new fetches beyond what Section 4 already adds:

- **"Since you opened this page"**: capture the first non-null `tier_remaining`/`blocked_total` seen this
  page-load as a baseline (a `useRef`, set once inside `load()`'s success path when the ref is still
  unset). Every later render shows `baseline - status.tier_remaining` (and same for `blocked_total`) —
  `0` or negative-safe (a `Math.max(0, ...)` guard — `blocked_total` climbing back up because a fresh page
  of unreviewed pairs got surfaced is possible and shouldn't render as a negative "progress").
- **"From that submit"**: computed inline in `runSubmit`'s new `else` branch (Section 4) by diffing the
  *previous* `status` (read from the closed-over `status` state before `setStatus` overwrites it) against
  `freshStatus`, for `tier_remaining` specifically (the number the reviewer is actively watching for the
  tier they're in). Surfaced through the same slot `formatResolveSummary`'s post-submit message already
  uses (`setError(...)`, despite the name — it's the page's one shared "here's what just happened" line),
  appended rather than replacing a move-failed/partial-failure summary if one is also present this submit.

`StatusBanner` (`:141-159`) changes from dumping `stats` as its only content to showing, when
`tier_remaining !== null`: `"N images still need {TIER_LABEL} review"` and `"N blocked from promotion"` as
the primary line, the "since you opened this page" delta beneath it, and the existing intake `stats`
dict demoted to a secondary/smaller line beneath both (kept, not removed — still real history, just no
longer presented as progress).

## Testing

- **Repository unit/integration** (`tests/integration/test_ingestion_*`, extend the existing tier-B or a
  new small file): a batch with a mix of reviewed/unreviewed, in-band/out-of-band, pending/active/rejected
  pairs across both tiers — `count_unreviewed_subjects` and `unreviewed_subject_ids` return exactly the
  expected subject sets per tier; a pending image with an open pair in *both* tiers' bands is counted once
  in the union, not twice, when computing what would become `blocked_total`.
- **Service unit** (`Backend/tests/test_ingestion_service.py`, mocked repo): `get_run_status` returns
  `tier_remaining`/`blocked_total` as `None` when `stage` is `hash_dedup`/`ocr_prepass`; returns real
  integers (from the mocked repo calls) when `stage` is `tier_a_review`/`tier_b_review`/`promoted`; calls
  `count_unreviewed_subjects` with the correct tier+band for the current stage and `unreviewed_subject_ids`
  for both tiers regardless of which one is "current" (blocked total is always batch-wide).
- **Endpoint** (`Backend/tests/test_ingestion_endpoints.py`): `GET /api/ingestion/run` response includes
  the two new fields, passthrough from the mocked service.
- **Frontend** (`IngestionReviewPage.test.tsx`): a submit that leaves units visible (not the last one)
  triggers a `getIngestionRunStatus()` call and updates the banner's `tier_remaining` text without
  triggering `getIngestionClusters`/`getIngestionTierBReview` again (proving it's the light refresh, not a
  full `load()`); the "since you opened this page" delta reflects the baseline captured on initial load;
  a `getIngestionRunStatus()` failure during that light refresh doesn't set the page-level error state
  (the existing move-failed/partial-failure summary path is unaffected) and doesn't throw.
- `tsc -b`, `eslint src/` (0 warnings), `vitest run`; regenerated-type `git diff` check across all three
  generator trees.

## Rollout

Additive: new repository methods, two new nullable response fields, one new frontend fetch call on an
existing endpoint, banner UI change. No schema/migration, no change to `resolve`/tier bands/cursor
behavior, no new endpoint. Ships as its own small PR off `main`.
