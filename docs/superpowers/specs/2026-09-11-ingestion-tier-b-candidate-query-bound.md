# Ingestion Review — Tier B Candidate Query Bound

status: done
Plan: docs/superpowers/plans/2026-09-11-ingestion-tier-b-candidate-query-bound.md
Originates from: docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md's final
whole-branch review (2026-09-10/11) — Important finding #3, deferred there as a tracked follow-up rather
than reopening that branch. No separate review file exists; the finding is reproduced in full below.
Sibling: docs/superpowers/specs/2026-09-11-ingestion-tier-b-review-partial-index.md — same originating
review, same branch's Recommendations section, a different query (Query A, not Query B). Independent
designs, no dependency either direction; either can ship without the other.

## Problem

`IngestionRepository.list_tier_b_review_page`'s Query B (the per-page candidate fetch) has no per-subject
limit — it returns *every* unreviewed in-band `tmp_duplicates` row for the page's subjects. The response is
correctly bounded: `IngestionService.list_tier_b_review` sorts each subject's candidate list in Python and
truncates it to `CANDIDATE_CAP` (30) before returning it, and `total_candidates` (from Query A) reports the
uncapped count. So this is not a correctness bug — the API contract holds exactly as documented. It's a
wasted-transfer bug: Query B moves rows into the app that get thrown away, on exactly the query that most
needs to be cheap.

Why it's not hypothetical: Query A ranks the page's subjects by their *tightest* candidate distance, so
page 1 is always the most duplicate-dense images in the batch — precisely the template-neighbourhood images
that motivated per-image review in the first place (one meme reposted hundreds of times; see the per-image
review spec's Problem section). On `general`'s live batch (~232k in-band pair rows), a `limit=200` fetch of
page 1 can transfer a large fraction of that pair set into Query B before 200 subjects × 30 = 6,000
candidates make it into the response — the query built to replace an endpoint that "never returns" doing
much more DB work than its own response size would suggest.

## Goal

Bound Query B server-side to the same "30 tightest per subject" the response already enforces in Python,
so the database and the wire only ever move what a page actually needs. No behavior change — same rows,
same order, same response shape.

## Non-goals

- Any change to the response shape, `CANDIDATE_CAP`'s value (30), `total_candidates` (stays the uncapped
  count from Query A), or the cursor (`(min_distance, subject_id)`, unaffected — it's entirely a Query A
  concern).
- Query A (the subject-ranking query) itself — it already aggregates per-subject minima via `GROUP BY` and
  never returns a per-candidate row, so it isn't part of this transfer problem.
- The partial index on `tmp_duplicates(tier_b_reviewed_at) WHERE tier_b_reviewed_at IS NULL` the same
  review separately recommended for Query A's full-232k-row aggregation cost. That's a different query and
  a different justification (index-assisted scan vs. capping an unbounded fetch) — track it as its own
  follow-up if it's wanted, not folded in here.
- Tier A / `list_clusters` — untouched; it has its own cap (`CLUSTER_MEMBER_CAP`) via a different code path
  and was never part of this query.

## Key facts this rests on

- Query B currently: `SELECT ... FROM pair p JOIN images c ON c.id = p.cand_id WHERE p.subject_id =
  ANY(:page_ids) ORDER BY p.subject_id, p.distance, p.cand_id` — no `LIMIT`, no per-partition bound.
- The service's current cap (`Backend/app/services/ingestion_service.py`, inside `list_tier_b_review`):
  ```python
  cands_by_subject: dict = {}
  for c in candidates:
      cands_by_subject.setdefault(c.subject_id, []).append(c)

  shown_cand_ids: set = set()
  for lst in cands_by_subject.values():
      lst.sort(key=lambda c: (c.distance, str(c.cand_id)))
      del lst[CANDIDATE_CAP:]
      shown_cand_ids.update(c.cand_id for c in lst)
  ```
  `CANDIDATE_CAP = 30` is already a module constant in that file.
- The tie-break `str(c.cand_id)` matters: it's what keeps a subject's candidate ordering deterministic
  when two candidates sit at the same `distance`. Any SQL-side replacement must reproduce it, or a subject
  near the cap boundary could show a different 30 candidates than before, in a different order, across
  otherwise-identical requests.

## Design

### 1. `IngestionRepository.list_tier_b_review_page` — bound Query B with a window function

Add a `candidate_cap: int` parameter. Query B changes from a flat `SELECT` to a ranked CTE that windows
each subject's candidates before the join, then filters to the top `candidate_cap`:

```python
async def list_tier_b_review_page(self, batch_id, low: float, high: float, cursor, limit: int,
                                   candidate_cap: int):
    ...  # pair_cte, having, subjects_sql unchanged

    page_ids = [r.subject_id for r in subjects[:limit]]
    cand_sql = text(pair_cte + """
    , ranked AS (
        SELECT p.*, ROW_NUMBER() OVER (
            PARTITION BY p.subject_id ORDER BY p.distance, p.cand_id::text
        ) AS rn
        FROM pair p
        WHERE p.subject_id = ANY(:page_ids)
    )
    SELECT r.subject_id, r.cand_id, c.filename AS cand_filename, c.status AS cand_status,
           r.distance, r.match_source
    FROM ranked r JOIN images c ON c.id = r.cand_id
    WHERE r.rn <= :candidate_cap
    ORDER BY r.subject_id, r.distance, r.cand_id
    """)
    candidates = (await self.session.execute(
        cand_sql, {**params, "page_ids": page_ids, "candidate_cap": candidate_cap})).all()
    return subjects, candidates
```

`pair_cte` is a `WITH pair AS (...)` string (see the existing implementation); appending `, ranked AS
(...)` after it chains a second CTE off the first, so the combined text reads `WITH pair AS (...), ranked
AS (...) SELECT ...` — valid Postgres, no change needed to `pair_cte` itself.

`ORDER BY p.distance, p.cand_id::text` inside the window is the same key the service's Python `sort`
currently uses (`(c.distance, str(c.cand_id))`), so the 30 rows Query B now returns for a capped subject
are byte-identical in content, order, and tie-break to what the old flat-fetch-then-Python-truncate path
produced. The final `ORDER BY r.subject_id, r.distance, r.cand_id` on the outer `SELECT` matches the
existing outer ordering (unchanged).

### 2. `IngestionService.list_tier_b_review` — pass the cap through, drop the now-redundant Python cap

```python
subjects, candidates = await self.repo.list_tier_b_review_page(
    resolved_id, low, high, decoded, limit, CANDIDATE_CAP)

...

cands_by_subject: dict = {}
shown_cand_ids: set = set()
for c in candidates:
    cands_by_subject.setdefault(c.subject_id, []).append(c)
    shown_cand_ids.add(c.cand_id)
```

Query B now returns each subject's candidates pre-sorted and pre-capped at exactly `candidate_cap` rows —
the Python `sort` + `del lst[CANDIDATE_CAP:]` loop has nothing left to do. Removing it isn't just cleanup:
keeping it would leave two enforcement points for the same cap (SQL bind + a dead Python truncate that
never trims anything), which is the kind of duplicated-authority code a later change could silently
de-sync — e.g. someone bumps `CANDIDATE_CAP` and only updates one side. `CANDIDATE_CAP` stays a single
module constant; it just has one caller (the repo call) instead of two.

`total_candidates` is untouched — still `s.total_candidates`, Query A's uncapped `COUNT(*)`.

### Interface change

`list_tier_b_review_page(self, batch_id, low, high, cursor, limit, candidate_cap)` — one new parameter.
Its only caller is `IngestionService.list_tier_b_review`, updated in the same change.

## Testing

- **Repository integration** (`tests/integration/test_ingestion_tier_b_review.py`): none of the existing
  fixtures give a subject more than a handful of candidates. Add a case with a subject wired to
  `CANDIDATE_CAP + N` (e.g. 40) candidate pairs at distinct distances and assert Query B returns exactly
  `CANDIDATE_CAP` rows for that subject, ordered `(distance, cand_id)` ascending, matching the
  tightest-`CANDIDATE_CAP` slice of the full set computed independently in the test. Also assert a subject
  under the cap is returned in full (regression: the `WHERE r.rn <= :candidate_cap` filter must not drop
  anything for the common case).
- **Repository integration**: the existing `test_lists_pending_subjects_with_candidates_ordered_by_tightest`
  and `test_cursor_pages_disjoint_subjects` cases must stay green unmodified — they exercise subjects well
  under the cap, so this change should be invisible to them; run them as a regression check, don't rewrite
  them.
- **Service unit** (`Backend/tests/test_ingestion_service.py::TestListTierBReview`): update
  `test_candidates_capped_total_reports_uncapped` — it currently hands the service a mocked repo response
  with `CANDIDATE_CAP + 10` candidate rows and asserts the *service* sorts and truncates them. Once the
  service no longer sorts/truncates, that test's premise (the service does the capping) is wrong; change
  the mock to return exactly `CANDIDATE_CAP` pre-sorted rows (as the repo now would) and assert the service
  passes them through unchanged, plus `total_candidates` still reports the mock's uncapped row count. Add a
  new assertion or test name making explicit that the service no longer re-sorts/re-truncates what the repo
  hands it — that invariant moved to the repo and should be locked in on both sides.
- No response-shape test changes — `TierBReviewItem`/`TierBReviewPage`/the endpoint tests are unaffected;
  this change is entirely below the service's return contract.
- `cd Backend && pytest -q` (full suite) + the tier-B integration file + `-k "ingestion or clusterize"` full
  integration slice (tier A untouched, but shares the repository module).

## Rollout

Pure query rewrite + a small service-side simplification. No schema change, no migration, no API contract
change, no frontend change. Low risk, ships as its own small PR/commit off `main`.
