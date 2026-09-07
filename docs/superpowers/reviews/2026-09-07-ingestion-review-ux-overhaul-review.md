# Ingestion Review UX Overhaul — Review

Spec: `docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md`
Plan: `docs/superpowers/plans/2026-09-07-ingestion-review-ux-overhaul.md`
Branch: `ingestion-review-ux-overhaul` (off `main` @ `55bf18f`)
Reviewed range: `55bf18f..695d4e6` (14 commits — 13 tasks + 1 final-review fix wave).
Method: subagent-driven development — per-task spec+quality review after each of 8 tasks, then a whole-branch review on the most capable model.

## Outcome

All 8 planned tasks implemented and merged onto the branch. Backend suite 280 passed;
`-k ingestion` 31 passed; `tests/integration/test_backend_ingestion_repository.py` 21 passed.
Frontend `tsc -b` clean, `eslint src/` 0 warnings, `vitest run` 196/196. No generated-type
drift. No DB migration required (`lang_score`/`confidence`/`distance` are pre-existing columns).

The endpoint response-shape change (`GET /api/ingestion/clusters/{tier}` now returns a
`ClusterPage`, not a bare list) is breaking, but its only consumer is the ingestion review
page — `grep -ril ingestion AndroidClient/` matches only the generated DTO file; there is no
Android ingestion screen, repository, or API call.

## What was fixed during the task loop

- **Task 2 review:** the `:.6f` cursor encoding could silently drop a cluster whose
  `min_distance` agreed with a neighbour's to 6 decimals but differed beyond — a stuck-run
  hazard for a review queue. Changed to `repr(float)` exact round-trip (Ruling 4); regression
  test added.
- **Task 5 review:** three `MemberTile` integration wrinkles (Enter double-fire when focus was
  on a decision button, preview flap when tabbing between buttons, image a11y) — fixed in
  Task 7 by moving hover/peek onto a real `<button>` wrapping the `<img>`.
- **Task 7 review (opus):** `submitting` was keyed by list index and broke under optimistic
  removal (a sibling cluster showed "Submitting…"); the Ruling-3 reload read a stale render
  closure; and the removed always-on decision-prune effect left a real gap (backend
  silently-skipped decisions). All three fixed — `submitting` keyed by cluster identity, refs
  for the reload gate, and a response-time payload-scoped prune that is strictly stronger
  than the effect it replaced.

## What the whole-branch review found, and what was done

**Important (all fixed in the final fix wave):**

1. `initialItemCount={clusters.length}` on `<Virtuoso>` rendered all of page 1 at mount,
   re-creating the "render everything at once" problem the branch exists to fix. It was a
   jsdom fallback made moot by the test mock. → prop deleted.
2. "Submit all" on a multi-page queue (`has_next` true) cleared every loaded cluster, which
   unmounted `<Virtuoso>` while both the empty-state message and the Ruling-3 reload stayed
   suppressed — leaving a near-blank page. → the reload is now gated on `remaining === 0`
   alone; a page-1 refetch is correct there.
3. On the happy path (no refetch), a partially-resolved cluster kept showing Keep/Reject for
   members the server had already resolved. → resolved members in surviving clusters now have
   their local `status` flipped to `active` in the same success handler, no extra request.

**Accepted deviations (spec amended, no code change):**

- `move_failed` entries are **not** rolled back on submit. This is correct — a `move_failed`
  image is durably rejected in the DB (only its file move failed); re-inserting it would show
  a resolved image as pending. Spec §6 step 4 and the data-flow block were amended to match
  the code.
- `get_ocr_texts` selects `OCRText.lang_score` but not `OCRText.language` (nothing consumes
  the label). Spec §2 amended.
- Ordering in `get_ocr_texts` is done in Python, not SQL `ORDER BY` (Ruling 2) — one testable
  source of truth. Spec §2 amended with the implementation note.
- `get_ocr_texts` thresholds: the final fix wave made them required parameters (removing the
  `0.4`/`0.3` defaults that could drift from `settings.yaml`) and updated the two
  integration-test call sites.

**Minor, fixed in the fix wave:** docked-pane vs action-bar z-index (`bar → z-50`); the
`lg:pr-[42vw]` gutter made permanent instead of toggling on hover (avoids list reflow);
`get_ocr_texts` threshold params made required (defaults removed, call sites updated);
`test_passes_ocr_thresholds_from_settings` now asserts against `settings.OCR.*` not literals;
`backend_api.md` cluster example made internally consistent (second member added); an
integration case for mixed `ru`/`en` `lang_score` rows + dedup; a comment near the cursor
filter about partial-resolve cluster reshaping.

The fix wave (`695d4e6`) was itself re-reviewed on the strong model: all 3 Important and all
selected Minors verified addressed, no new Critical/Important breakage. Backend 280 +
integration 22 passed; frontend `tsc`/`eslint` clean, `vitest` 198/198.

**One Minor introduced by the fix wave, parked (Ruling 6):** the "flip resolved members to
`active`" step (Important 3b) uses a blanket `status: "active"` for both rejected and kept
ids, so a member the server just **rejected** shows the status label `active` (instead of
`rejected`) until the next `load()`. Controls-hiding — the actual goal — is correct either
way, and nothing downstream keys off the string beyond the label. ~2-line follow-up: split
the resolved ids into rejected/kept sets and flip each accordingly.

**Minor, intentionally not addressed:**

- `_order_key` closure redefined per `get_ocr_texts` call — negligible, subjective.
- Tie-break order among OCR rows with equal `(lang_score, confidence)` is DB-undefined — same
  as before this branch; `sorted()` is stable so it preserves fetch order.
- `_decode_cursor` would restart the queue if a distance ever arrived as `Decimal` — provably
  unreachable (`Storage/models.py` declares `distance`/`confidence`/`lang_score` as `Float`,
  asyncpg yields `float`).
- No HTTP-level test for the `?limit=40&cursor=` URL string — `HttpMemesApi` has no test
  coverage anywhere in this repo; the URL is exercised indirectly through the page tests.
- Concurrent per-cluster submits share one `submitting` slot — payloads are provably disjoint
  (union-find components share no member), so no data hazard; only symptom is the
  first-to-settle clearing both spinners.
- `decidedPendingIn`'s `status === "pending"` filter is now defense-in-depth (the payload
  prune and reload prune are the primary staleness mechanism); covered by a user-visible test.

## Manual smoke

Not performed — no active `general` ingestion run was available in this environment. The two
things automated tests structurally cannot cover are the docked-pane hover latency (the whole
justification for rejecting a server-side thumbnail endpoint rests on the tile image already
being in browser cache) and the hover/reflow interaction. Recommend a manual pass against the
running `general` dev servers (`:5174` / `:8082`, never binding the always-occupied env ports)
before merge.
