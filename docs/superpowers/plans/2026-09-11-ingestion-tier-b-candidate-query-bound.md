# Tier B Candidate Query Bound Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound `IngestionRepository.list_tier_b_review_page`'s Query B (the per-page candidate fetch) to `CANDIDATE_CAP` tightest rows per subject in SQL, and drop the now-redundant Python sort/truncate in `IngestionService.list_tier_b_review` — same response, no wasted transfer.

**Architecture:** Add a `candidate_cap: int` parameter to the repository method; Query B's flat `SELECT` becomes a `ROW_NUMBER() OVER (PARTITION BY subject_id ORDER BY distance, cand_id)` window, filtered to `rn <= candidate_cap`, chained onto the existing `pair` CTE. The service passes its `CANDIDATE_CAP` constant through and groups the (already capped, already sorted) rows without re-sorting.

**Tech Stack:** FastAPI + SQLAlchemy async, raw `text()` SQL (window function), pytest + live-PG integration.

**Spec:** `docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md`

## Global Constraints

- `CANDIDATE_CAP = 30` — existing module constant in `Backend/app/services/ingestion_service.py:54`. Unchanged value; it just gets one caller (the repo call) instead of two (repo fetch + Python truncate).
- The window's tie-break `ORDER BY p.distance, p.cand_id::text` must exactly match the tie-break the Python code used before (`(c.distance, str(c.cand_id))`) — a subject near the cap boundary must return the identical 30 candidates, in the identical order, before and after this change.
- `total_candidates` — untouched, still `s.total_candidates` from Query A's uncapped `COUNT(*)`. Not part of this change at all.
- No response shape change: `TierBReviewItem`/`TierBReviewPage`/the endpoint stay byte-identical. No schema, no regenerated types, no `backend_api.md` edit.
- `cd Backend && pytest` on its own. Integration: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the CLI, never combined with `Backend/tests/` in one invocation.
- Tier A / `list_clusters` — untouched; this method has no tier-A caller.

---

## File Structure

**Modify:** `Backend/app/repositories/ingestion_repository.py` (Query B rewrite), `Backend/app/services/ingestion_service.py` (pass `candidate_cap`, drop the Python cap), `tests/integration/test_ingestion_tier_b_review.py` (new capped-candidates case + 3 existing call sites updated), `Backend/tests/test_ingestion_service.py` (update the now-inaccurate "service sorts/truncates" test).
**Docs — modify:** the spec's status line (final task).

---

## Task 1: Repository — bound Query B with a window function

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py:145-203` (`list_tier_b_review_page`)
- Modify: `tests/integration/test_ingestion_tier_b_review.py` (3 existing calls gain the new arg; 1 new test)

**Interfaces:**
- Consumes: nothing new — `Storage/models.py`'s `TmpDuplicates`/`Image` are unchanged.
- Produces: `IngestionRepository.list_tier_b_review_page(batch_id, low, high, cursor, limit, candidate_cap: int) -> (subjects, candidates)` — same return shape as today, `candidates` now holds at most `candidate_cap` rows per distinct `subject_id`, tightest-first.

- [ ] **Step 1: Update the 3 existing integration tests' call sites**

`candidate_cap` is a new required parameter — every existing call breaks until updated. In
`tests/integration/test_ingestion_tier_b_review.py`, add the import and update all 3 calls:

```python
from Backend.app.services.ingestion_service import CANDIDATE_CAP
```

- `test_lists_pending_subjects_with_candidates_ordered_by_tightest`: change
  `await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40)` to
  `await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)`.
- `test_excludes_reviewed_rejected_and_out_of_band`: same change to its
  `repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40)` call.
- `test_cursor_pages_disjoint_subjects`: same change to BOTH of its calls (`page1, _ = ...` and
  `page2, _ = ...`), keeping their existing `limit=2`.

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_tier_b_review.py -v`
Expected: FAIL — `TypeError: list_tier_b_review_page() missing 1 required positional argument: 'candidate_cap'` on all 5 existing tests (the 3 you just fixed will pass once Step 1 alone is done; run again to confirm those 3 are green before continuing — the 2 cascade tests from `test_ingestion_tier_b_review.py`'s Task 7 additions call `service.list_tier_b_review`, not the repo directly, so they're unaffected by this step and should already be passing).

- [ ] **Step 2: Write the new failing test — a subject over the cap**

Append to `tests/integration/test_ingestion_tier_b_review.py`:

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_candidates_capped_at_source_tightest_first(db_session):
    bid = await _run(db_session)
    subject = await _img(db_session, "pending", bid)
    cand_ids = [await _img(db_session, "active", bid) for _ in range(CANDIDATE_CAP + 10)]
    for i, cid in enumerate(cand_ids):
        await _pair(db_session, subject, cid, 0.05 + i * 0.001)  # distinct ascending distances, all in-band

    repo = IngestionRepository(db_session)
    subjects, candidates = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=None, limit=40, candidate_cap=CANDIDATE_CAP)

    subj_row = next(s for s in subjects if s.subject_id == subject)
    assert subj_row.total_candidates == CANDIDATE_CAP + 10           # uncapped count, from Query A

    subj_cands = [c for c in candidates if c.subject_id == subject]
    assert len(subj_cands) == CANDIDATE_CAP                          # capped at the source now
    assert [c.cand_id for c in subj_cands] == cand_ids[:CANDIDATE_CAP]  # tightest-first, by construction
```

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_tier_b_review.py::test_candidates_capped_at_source_tightest_first -v`
Expected: FAIL — `len(subj_cands) == 40` (all candidates returned, no source-side cap yet), not `30`.

- [ ] **Step 3: Rewrite Query B**

In `Backend/app/repositories/ingestion_repository.py`, replace the `list_tier_b_review_page` method
(lines 145-203) with:

```python
    async def list_tier_b_review_page(self, batch_id, low: float, high: float, cursor, limit: int,
                                       candidate_cap: int):
        """See docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md and
        docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md.
        `cursor` is (min_distance, subject_id_str) or None. Returns (subjects, candidates):
        subjects is up to limit+1 rows ordered by (min_distance, subject_id); candidates is, per
        subject, its `candidate_cap` tightest rows (tightest-first) -- not every candidate row;
        callers needing the uncapped count use `total_candidates` on the subject row."""
        # Each unreviewed in-band tmp_duplicates row contributes (subject_id, distance) for the
        # side that is a pending image of this batch, when the other side is not rejected.
        pair_cte = """
        WITH pair AS (
            SELECT td.image_id1 AS subject_id, td.image_id2 AS cand_id, td.distance, td.match_source
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id1
            JOIN images o ON o.id = td.image_id2
            WHERE td.tier_b_reviewed_at IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
            UNION ALL
            SELECT td.image_id2 AS subject_id, td.image_id1 AS cand_id, td.distance, td.match_source
            FROM tmp_duplicates td
            JOIN images s ON s.id = td.image_id2
            JOIN images o ON o.id = td.image_id1
            WHERE td.tier_b_reviewed_at IS NULL
              AND td.distance >= :low AND td.distance < :high
              AND s.ingestion_batch_id = :batch_id AND s.status = 'pending'
              AND o.status <> 'rejected'
        )
        """
        having = ""
        params = {"low": low, "high": high, "batch_id": batch_id, "limit": limit + 1}
        if cursor is not None:
            having = "HAVING (MIN(p.distance), p.subject_id::text) > (:cur_d, :cur_s)"
            params["cur_d"] = cursor[0]
            params["cur_s"] = cursor[1]

        subjects_sql = text(pair_cte + f"""
        SELECT p.subject_id, i.filename, i.status,
               MIN(p.distance) AS min_distance, COUNT(*) AS total_candidates
        FROM pair p JOIN images i ON i.id = p.subject_id
        GROUP BY p.subject_id, i.filename, i.status
        {having}
        ORDER BY MIN(p.distance), p.subject_id::text
        LIMIT :limit
        """)
        subjects = (await self.session.execute(subjects_sql, params)).all()
        if not subjects:
            return [], []

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

Note: `pair_cte` still opens with `WITH pair AS (...)`; appending `, ranked AS (...)` after it chains
a second CTE off the first, so the combined text reads `WITH pair AS (...), ranked AS (...) SELECT
...` — no change needed to `pair_cte` itself.

- [ ] **Step 4: Run to verify pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_tier_b_review.py -v`
Expected: PASS (all cases — the 3 existing repo-level tests, the 2 service-level cascade tests, and
the new capped-candidates test).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py tests/integration/test_ingestion_tier_b_review.py
git commit -m "feat: bound Query B's candidate fetch to CANDIDATE_CAP in SQL

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 2: Service — pass `CANDIDATE_CAP` through, drop the redundant Python cap

**Files:**
- Modify: `Backend/app/services/ingestion_service.py:241-282` (`list_tier_b_review`)
- Modify: `Backend/tests/test_ingestion_service.py:56-68` (`test_candidates_capped_total_reports_uncapped`)

**Interfaces:**
- Consumes: `IngestionRepository.list_tier_b_review_page(..., candidate_cap)` (Task 1).
- Produces: `IngestionService.list_tier_b_review` — same public signature and return shape as before;
  internal behavior only.

- [ ] **Step 1: Update the failing/changing unit tests**

In `Backend/tests/test_ingestion_service.py`, replace
`test_candidates_capped_total_reports_uncapped` (lines 56-68) — the service no longer sorts or
truncates, so a test asserting it does is now testing the wrong layer:

```python
    async def test_candidates_passthrough_from_repo_total_reports_uncapped(self, service, mock_repo):
        import uuid
        s1 = uuid.uuid4()
        # Simulates the repo's contract as of Task 1: candidates arrive already capped at
        # CANDIDATE_CAP and already tightest-first -- the service only groups them by subject.
        cands = [_cand(s1, uuid.uuid4(), 0.05 + i * 0.001) for i in range(CANDIDATE_CAP)]
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.list_tier_b_review_page.return_value = ([_subj(s1, 0.05, CANDIDATE_CAP + 10)], cands)
        mock_repo.get_ocr_texts.return_value = {}

        it = (await service.list_tier_b_review())["items"][0]
        assert len(it["candidates"]) == CANDIDATE_CAP
        assert it["total_candidates"] == CANDIDATE_CAP + 10          # uncapped count, from the subject row
        # passthrough, not re-sorted/re-truncated -- exactly the order the repo returned
        assert [c["distance"] for c in it["candidates"]] == [c.distance for c in cands]

    async def test_repo_called_with_candidate_cap(self, service, mock_repo):
        import uuid
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.list_tier_b_review_page.return_value = ([], [])
        mock_repo.get_ocr_texts.return_value = {}

        await service.list_tier_b_review()

        # positional call: (resolved_id, low, high, decoded, limit, candidate_cap)
        assert mock_repo.list_tier_b_review_page.call_args.args[5] == CANDIDATE_CAP
```

Run: `cd Backend && pytest tests/test_ingestion_service.py::TestListTierBReview -v`
Expected: FAIL — `test_candidates_passthrough_from_repo_total_reports_uncapped` fails (the mock hands
back exactly `CANDIDATE_CAP` rows and the *old* service code would still try to sort/truncate an
already-≤-cap list, which is harmless and would actually pass by accident on the count — the real
failure is `test_repo_called_with_candidate_cap`: `IndexError: tuple index out of range`, since the
old service call is only 5 positional args, no index 5 yet).

- [ ] **Step 2: Update the service**

In `Backend/app/services/ingestion_service.py`, in `list_tier_b_review` (lines 241-282):

Change the repo call:
```python
        subjects, candidates = await self.repo.list_tier_b_review_page(
            resolved_id, low, high, decoded, limit, CANDIDATE_CAP)
```

Replace the sort/cap block:
```python
        # The repo returns each subject's candidates pre-sorted and pre-capped at CANDIDATE_CAP
        # (see docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md) -- group
        # only, no re-sort/re-truncate here. CANDIDATE_CAP has exactly one enforcement point: the
        # repo call above.
        cands_by_subject: dict = {}
        shown_cand_ids: set = set()
        for c in candidates:
            cands_by_subject.setdefault(c.subject_id, []).append(c)
            shown_cand_ids.add(c.cand_id)
```

(This replaces the old `cands_by_subject.setdefault(...).append(...)` loop over `candidates` PLUS the
following `shown_cand_ids: set = set()` / `for lst in cands_by_subject.values(): lst.sort(...); del
lst[CANDIDATE_CAP:]; shown_cand_ids.update(...)` block — both collapse into the single loop above.)

Everything else in the method (`has_next`, `page`, `subject_ids`, the `ocr` fetch, `member()`, the
`items` comprehension, `next_cursor`, the return) is unchanged.

- [ ] **Step 3: Run to verify pass**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`
Expected: PASS — `TestListTierBReview` (now 5 tests) plus every other existing service test.

- [ ] **Step 4: Full backend + integration sweep**

```bash
cd Backend && pytest -q
```
Expected: PASS.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q -k "ingestion or clusterize"
```
Expected: PASS — including the existing cluster/split integration tests (tier A untouched; this
method has no tier-A caller).

- [ ] **Step 5: Spec status + commit**

`docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md`: `status: draft` →
`status: done`; add `Plan: docs/superpowers/plans/2026-09-11-ingestion-tier-b-candidate-query-bound.md`
under the status line.

```bash
git add Backend/app/services/ingestion_service.py Backend/tests/test_ingestion_service.py \
        docs/superpowers/specs/2026-09-11-ingestion-tier-b-candidate-query-bound.md
git commit -m "feat: drop the redundant Python candidate cap; mark spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

- [ ] **Step 6: Whole-branch review**

Invoke `superpowers:requesting-code-review` for the branch against the spec. One iteration; fix action
points; post the final summary. Pay particular attention to: the window function's tie-break matching
the old Python sort exactly (a subject near the cap boundary must return identical candidates, identical
order, before and after), and that `total_candidates` genuinely never changed (it was never part of this
diff).

---

## Self-Review (completed during planning)

**Spec coverage:**
- Design §1 (Query B rewrite) → Task 1.
- Design §2 (drop the Python cap) → Task 2.
- "Key facts" tie-break requirement → Task 1 Step 3's `ORDER BY p.distance, p.cand_id::text`, verified
  against the exact pre-change Python key `(c.distance, str(c.cand_id))`.
- Testing: the >30-candidates case → Task 1 Step 2; the under-cap regression → Task 1 Step 1 re-running
  the 3 existing repo tests; the service passthrough test → Task 2 Step 1; the full sweep → Task 2 Step 4.
- Non-goals respected: no schema/migration, no response shape change, no frontend touch, Tier A untouched
  (no task in this plan touches `list_clusters`, `ClusterRow`, or any tier-A path).

**Placeholder scan:** none. Every code step has literal code (the full replacement method body in Task 1
Step 3, the full test bodies in both tasks' Step 1); every test step names the command + expected result,
including the specific failure mode expected at each RED step.

**Type consistency:** `list_tier_b_review_page(batch_id, low, high, cursor, limit, candidate_cap) ->
(subjects, candidates)` — the new parameter's name and position (last, positional-or-keyword) match
across Task 1's implementation, Task 1's 4 test call sites, and Task 2's service call
(`self.repo.list_tier_b_review_page(resolved_id, low, high, decoded, limit, CANDIDATE_CAP)`). Row field
names (`subject_id`, `cand_id`, `cand_filename`, `cand_status`, `distance`, `match_source`) unchanged from
the pre-existing contract, so Task 2's `member()`/items-building code needs no changes beyond the two
blocks named in Step 2.
