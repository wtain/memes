# Per-Image Tier B Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the degenerate cluster model for tier B with a per-image review queue — one card per pending image plus its unreviewed candidate neighbours — served by a new endpoint.

**Architecture:** New repo method (two SQL queries: rank+page the subject images, then fetch candidates for the page's subjects only) → new service method → new `GET /api/ingestion/review/tier_b` endpoint returning `{items: [{image, candidates, total_candidates}], next_cursor, has_next}`. Frontend: new `getIngestionTierBReview` client, new `TierBReviewCard`, and `IngestionReviewPage` branches on `tier` — tier A keeps `ClusterRow` byte-for-byte, tier B renders the new cards. `resolve` and the optimistic-submit machinery are reused; two shape-switching helpers (`decidedPendingIn`, `isFullyResolved`) generalise over `cluster | tier-b item`.

**Tech Stack:** FastAPI + SQLAlchemy async, asyncpg row-value cursor, pytest + live-PG integration; React 19 + TS + Tailwind + react-virtuoso + Vitest; hand-written JSON Schema → generated TS/Kotlin.

**Spec:** `docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md`

## Global Constraints

- **`CANDIDATE_CAP = 30`** — module constant in `ingestion_service.py`; candidates per subject are capped to the 30 tightest, `total_candidates` reports the uncapped count.
- **Cursor:** `(min_distance, subject_id)`, encoded with the existing `_encode_cursor(min_distance, str(subject_id))` (`repr(float)|uuid`), decoded with `_decode_cursor`. Query A's cursor filter is a Postgres row-value comparison in `HAVING`: `HAVING (min(distance), subject_id) > (:cursor_distance, :cursor_subject_id)`, omitted when there is no cursor. Malformed/blank cursor → from the start (never 4xx). `limit` default 40, `Query(40, ge=1, le=200)`.
- **`resolve` / `mark_reviewed` / `reject_image` / `get_blocked_pending_ids` / `ingest_promote` / `tmp_duplicates` schema / tier bands — NOT changed.** Decisions still `POST /api/ingestion/clusters/tier_b/resolve` with `[{image_id, decision}]`.
- Tier B band = `[settings.DUPLICATES.threshold_low_for_tier_b … settings.DUPLICATES.THRESHOLD)` — reuse `_tier_band("tier_b")` for `(low, high)`.
- Subject set = **pending** images of this batch with ≥1 `tmp_duplicates` row where `tier_b_reviewed_at IS NULL`, `distance` in band, and the *other* image not `rejected`. Mirrors `get_tier_candidate_rows`.
- Tier A path (`list_clusters`, `ClusterRow`, `getIngestionClusters`) must stay behavior-identical.
- `cd Backend && pytest` on its own. Integration: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"` on the CLI.
- Generated files regenerated + committed with the schema change. `backend_api.md` updated in the endpoint task.
- Frontend gate: `tsc -b`, `eslint src/` (0 warnings), `vitest run`.
- New generated types: `IngestionTierBReviewPage`, `IngestionTierBReviewItem`, `IngestionTierBCandidate`.

---

## File Structure

**Backend — create:** `tests/integration/test_ingestion_tier_b_review.py`
**Backend — modify:** `Backend/app/repositories/ingestion_repository.py` (new method), `Backend/app/services/ingestion_service.py` (new method + `CANDIDATE_CAP`), `Backend/app/api/ingestion.py` (models + endpoint), `Backend/tests/test_ingestion_service.py` (new class), `Backend/tests/test_ingestion_endpoints.py` (new class), `backend_api.md`.
**Shared — create:** `shared/schemas/ingestiontierbcandidate.schema.json`, `ingestiontierbreviewitem.schema.json`, `ingestiontierbreviewpage.schema.json`. **Modify:** `shared/schemas/all.schema.json`.
**Generated (regenerated, not hand-edited) — all THREE trees off `all.schema.json`:** `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/**/Models.kt`, **and** `Backend/app/types/generated/` (`datamodel-codegen`, per `documents/generation.md` — the stopgap and 2026-09-07 branches forgot this one; do NOT skip it).
**Frontend — create:** `src/components/ingestion/TierBReviewCard.tsx`, `TierBReviewCard.test.tsx`.
**Frontend — modify:** `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/pages/IngestionReviewPage.tsx`, `src/pages/IngestionReviewPage.test.tsx`, `src/types/generated/all.d.ts` (regen), `AndroidClient/**` (regen).
**Docs — modify:** the spec's status line (final task).

---

## Task 1: Repository — `list_tier_b_review_page`

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py`
- Create: `tests/integration/test_ingestion_tier_b_review.py`

**Interfaces:**
- Produces: `IngestionRepository.list_tier_b_review_page(batch_id, low, high, cursor, limit) -> (subjects, candidates)` where
  - `subjects`: `list[Row(subject_id: UUID, filename: str, status: str, min_distance: float, total_candidates: int)]` — the page (up to `limit`), ordered by `(min_distance, subject_id)`, filtered past `cursor` (a `tuple[float, str] | None`).
  - `candidates`: `list[Row(subject_id: UUID, cand_id: UUID, cand_filename: str, cand_status: str, distance: float, match_source: str | None)]` for exactly the page's subjects, ordered `(subject_id, distance, cand_id)`.
  - `has_next` is derived by the caller from `len(subjects)` vs `limit` — the method fetches `limit + 1` subjects and returns all of them (caller slices).

- [ ] **Step 1: Write the failing integration test**

Create `tests/integration/test_ingestion_tier_b_review.py`:

```python
"""Integration tests for the per-image Tier B review queue. Live PostgreSQL (see conftest)."""
import uuid

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository
from repository.batch_runs import BatchRunRepository
from Storage.models import Image, TmpDuplicates

LOW, HIGH = 0.05, 0.30


async def _run(session):
    return await BatchRunRepository(session).create_run(kind="ingestion", trigger="manual", stage="tier_b_review")


async def _img(session, status, batch_id):
    i = Image(filename=f"{uuid.uuid4()}.jpg", status=status, ingestion_batch_id=batch_id)
    session.add(i); await session.flush(); return i.id


async def _pair(session, a, b, d):
    session.add(TmpDuplicates(image_id1=min(a, b), image_id2=max(a, b), distance=d, match_source="in_batch"))
    await session.flush()


@pytest.mark.asyncio(loop_scope="session")
async def test_lists_pending_subjects_with_candidates_ordered_by_tightest(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    p3 = await _img(db_session, "pending", bid)          # no tier-b pair -> not a subject
    active = await _img(db_session, "active", bid)
    await _pair(db_session, p1, active, 0.20)
    await _pair(db_session, p1, p2, 0.08)                 # p1's tightest
    await _pair(db_session, p2, active, 0.15)
    _ = p3

    repo = IngestionRepository(db_session)
    subjects, candidates = await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40)

    sids = [s.subject_id for s in subjects]
    assert sids == [p1, p2]                               # p1 (min 0.08) before p2 (min 0.15)
    assert subjects[0].min_distance == pytest.approx(0.08)
    assert subjects[0].total_candidates == 2
    p1_cands = sorted((c for c in candidates if c.subject_id == p1), key=lambda c: c.distance)
    assert [c.cand_id for c in p1_cands] == [p2, active]  # tightest first
    assert all(c.subject_id in {p1, p2} for c in candidates)   # only page subjects


@pytest.mark.asyncio(loop_scope="session")
async def test_excludes_reviewed_rejected_and_out_of_band(db_session):
    bid = await _run(db_session)
    p = await _img(db_session, "pending", bid)
    rej = await _img(db_session, "rejected", bid)
    o1 = await _img(db_session, "active", bid)
    o2 = await _img(db_session, "active", bid)
    await _pair(db_session, p, rej, 0.10)                 # other side rejected -> excluded
    await _pair(db_session, p, o1, 0.40)                  # out of band -> excluded
    reviewed = TmpDuplicates(image_id1=min(p, o2), image_id2=max(p, o2), distance=0.10,
                             match_source="in_batch")
    from datetime import datetime, timezone
    reviewed.tier_b_reviewed_at = datetime.now(timezone.utc)
    db_session.add(reviewed); await db_session.flush()    # already reviewed -> excluded

    repo = IngestionRepository(db_session)
    subjects, _ = await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=40)
    assert subjects == []


@pytest.mark.asyncio(loop_scope="session")
async def test_cursor_pages_disjoint_subjects(db_session):
    bid = await _run(db_session)
    active = await _img(db_session, "active", bid)
    subs = []
    for i in range(5):
        p = await _img(db_session, "pending", bid)
        await _pair(db_session, p, active, 0.10 + i * 0.01)
        subs.append(p)

    repo = IngestionRepository(db_session)
    page1, _ = await repo.list_tier_b_review_page(bid, LOW, HIGH, cursor=None, limit=2)
    assert len(page1) == 3                                # limit + 1
    boundary = page1[1]                                   # 2nd item is the last of page 1
    page2, _ = await repo.list_tier_b_review_page(
        bid, LOW, HIGH, cursor=(boundary.min_distance, str(boundary.subject_id)), limit=2)
    assert {s.subject_id for s in page1[:2]}.isdisjoint({s.subject_id for s in page2})
```

- [ ] **Step 2: Run to verify failure**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_tier_b_review.py -v`
Expected: FAIL — `list_tier_b_review_page` not defined.

- [ ] **Step 3: Implement the method**

Add to `IngestionRepository` (uses `from sqlalchemy import text` — this method uses raw SQL for the `UNION ALL` + row-value `HAVING`, which is awkward in the ORM; the rest of the file's ORM style is unaffected):

```python
    async def list_tier_b_review_page(self, batch_id, low: float, high: float, cursor, limit: int):
        """See docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md.
        `cursor` is (min_distance, subject_id_str) or None. Returns (subjects, candidates):
        subjects is up to limit+1 rows ordered by (min_distance, subject_id); candidates is
        every candidate row for those subjects."""
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
        ORDER BY MIN(p.distance), p.subject_id
        LIMIT :limit
        """)
        subjects = (await self.session.execute(subjects_sql, params)).all()
        if not subjects:
            return [], []

        page_ids = [r.subject_id for r in subjects[:limit]]
        cand_sql = text(pair_cte + """
        SELECT p.subject_id, p.cand_id, c.filename AS cand_filename, c.status AS cand_status,
               p.distance, p.match_source
        FROM pair p JOIN images c ON c.id = p.cand_id
        WHERE p.subject_id = ANY(:page_ids)
        ORDER BY p.subject_id, p.distance, p.cand_id
        """)
        candidates = (await self.session.execute(
            cand_sql, {**params, "page_ids": page_ids})).all()
        return subjects, candidates
```

Add `from sqlalchemy import text` to the imports if not present.

Note: `p.subject_id::text` in the `HAVING` matches the `_encode_cursor` id string; the row-value `>` compares `min_distance` first (float), then the id text — the same total order the service's `_sort_key` uses. `total_candidates` counts every side, which equals the number of distinct candidate rows because each `tmp_duplicates` row appears once per subject direction.

- [ ] **Step 4: Run to verify pass**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_tier_b_review.py -v`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py tests/integration/test_ingestion_tier_b_review.py
git commit -m "feat: repo query for the per-image Tier B review queue

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 2: Service — `IngestionService.list_tier_b_review`

**Files:**
- Modify: `Backend/app/services/ingestion_service.py`
- Modify: `Backend/tests/test_ingestion_service.py` (add `TestListTierBReview`)

**Interfaces:**
- Consumes: `repo.list_tier_b_review_page(batch_id, low, high, cursor, limit) -> (subjects, candidates)` (Task 1); `repo.get_ocr_texts(ids, conf_min, lang_min)`; `_tier_band`, `_encode_cursor`, `_decode_cursor` (existing).
- Produces: `IngestionService.list_tier_b_review(batch_id=None, cursor=None, limit=40) -> dict`:
  ```
  {"items": [{"image": {image_id, filename, status, ocr_text},
              "candidates": [{"member": {image_id, filename, status, ocr_text},
                              "distance": float, "match_source": str | None}],
              "total_candidates": int}],
   "next_cursor": str | None, "has_next": bool}
  ```
- Produces: `CANDIDATE_CAP = 30` (module constant).

- [ ] **Step 1: Write the failing unit test**

Add to `Backend/tests/test_ingestion_service.py`:

```python
from types import SimpleNamespace
from Backend.app.services.ingestion_service import CANDIDATE_CAP


def _subj(sid, mind, total):
    return SimpleNamespace(subject_id=sid, filename=f"{sid}.jpg", status="pending",
                           min_distance=mind, total_candidates=total)


def _cand(sid, cid, dist, status="pending", src="in_batch"):
    return SimpleNamespace(subject_id=sid, cand_id=cid, cand_filename=f"{cid}.jpg",
                           cand_status=status, distance=dist, match_source=src)


class TestListTierBReview:
    async def test_item_shape_and_ocr(self, service, mock_repo):
        import uuid
        s1 = uuid.uuid4(); c1 = uuid.uuid4()
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.list_tier_b_review_page.return_value = ([_subj(s1, 0.08, 1)], [_cand(s1, c1, 0.08)])
        mock_repo.get_ocr_texts.return_value = {s1: "subj text", c1: "cand text"}

        page = await service.list_tier_b_review(limit=40)

        it = page["items"][0]
        assert it["image"] == {"image_id": str(s1), "filename": f"{s1}.jpg",
                               "status": "pending", "ocr_text": "subj text"}
        assert it["candidates"][0]["member"]["ocr_text"] == "cand text"
        assert it["candidates"][0]["distance"] == 0.08
        assert it["total_candidates"] == 1
        assert page["has_next"] is False and page["next_cursor"] is None
        # OCR fetched for subject + candidate
        args = mock_repo.get_ocr_texts.call_args.args
        assert set(args[0]) == {s1, c1}
        assert args[1:] == (0.4, 0.3)

    async def test_candidates_capped_total_reports_uncapped(self, service, mock_repo):
        import uuid
        s1 = uuid.uuid4()
        cands = [_cand(s1, uuid.uuid4(), 0.05 + i * 0.001) for i in range(CANDIDATE_CAP + 10)]
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.list_tier_b_review_page.return_value = ([_subj(s1, 0.05, CANDIDATE_CAP + 10)], cands)
        mock_repo.get_ocr_texts.return_value = {}

        it = (await service.list_tier_b_review())["items"][0]
        assert len(it["candidates"]) == CANDIDATE_CAP
        assert it["total_candidates"] == CANDIDATE_CAP + 10
        # kept the tightest
        assert [c["distance"] for c in it["candidates"]] == sorted(c.distance for c in cands)[:CANDIDATE_CAP]

    async def test_has_next_and_cursor_roundtrip(self, service, mock_repo):
        import uuid
        subs = [_subj(uuid.uuid4(), 0.05 + i * 0.01, 1) for i in range(3)]
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.list_tier_b_review_page.return_value = (subs, [])  # 3 subjects, limit 2 -> has_next
        mock_repo.get_ocr_texts.return_value = {}

        page = await service.list_tier_b_review(limit=2)
        assert len(page["items"]) == 2 and page["has_next"] is True
        from Backend.app.services.ingestion_service import _decode_cursor
        assert _decode_cursor(page["next_cursor"]) == (subs[1].min_distance, str(subs[1].subject_id))

    async def test_blank_cursor_starts_from_beginning(self, service, mock_repo):
        import uuid
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.list_tier_b_review_page.return_value = ([], [])
        mock_repo.get_ocr_texts.return_value = {}
        for bad in ("", "   ", "nope", "0.1|"):
            await service.list_tier_b_review(cursor=bad)  # must not raise
            _, kwargs = mock_repo.list_tier_b_review_page.call_args
            assert kwargs.get("cursor") is None or mock_repo.list_tier_b_review_page.call_args.args[-2] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Backend && pytest tests/test_ingestion_service.py::TestListTierBReview -v`
Expected: FAIL — `list_tier_b_review` / `CANDIDATE_CAP` not defined.

- [ ] **Step 3: Implement**

`ingestion_service.py`, module-level near `CLUSTER_MEMBER_CAP` (or `_split_params`):

```python
CANDIDATE_CAP = 30
```

Method on `IngestionService`:

```python
    async def list_tier_b_review(self, batch_id: Optional[UUID] = None,
                                 cursor: Optional[str] = None, limit: int = 40) -> dict:
        resolved_id = await self._resolve_batch_id(batch_id)
        low, high = _tier_band("tier_b")
        decoded = _decode_cursor(cursor)
        subjects, candidates = await self.repo.list_tier_b_review_page(
            resolved_id, low, high, decoded, limit)

        has_next = len(subjects) > limit
        page = subjects[:limit]

        cands_by_subject: dict = {}
        for c in candidates:
            cands_by_subject.setdefault(c.subject_id, []).append(c)

        shown_cand_ids: set = set()
        for lst in cands_by_subject.values():
            lst.sort(key=lambda c: (c.distance, str(c.cand_id)))
            del lst[CANDIDATE_CAP:]
            shown_cand_ids.update(c.cand_id for c in lst)

        subject_ids = {s.subject_id for s in page}
        ocr = await self.repo.get_ocr_texts(
            subject_ids | shown_cand_ids, settings.OCR.CONFIDENCE_MIN, settings.OCR.LANG_SCORE_MIN)

        def member(image_id, filename, status):
            return {"image_id": str(image_id), "filename": filename,
                    "status": status, "ocr_text": ocr.get(image_id)}

        items = [{
            "image": member(s.subject_id, s.filename, s.status),
            "candidates": [
                {"member": member(c.cand_id, c.cand_filename, c.cand_status),
                 "distance": c.distance, "match_source": c.match_source}
                for c in cands_by_subject.get(s.subject_id, [])
            ],
            "total_candidates": s.total_candidates,
        } for s in page]

        next_cursor = (_encode_cursor(page[-1].min_distance, str(page[-1].subject_id))
                       if (page and has_next) else None)
        return {"items": items, "next_cursor": next_cursor, "has_next": has_next}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`
Expected: PASS — new class plus all existing service tests green.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/services/ingestion_service.py Backend/tests/test_ingestion_service.py
git commit -m "feat: IngestionService.list_tier_b_review

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 3: API — endpoint, models, schemas, regenerated types, docs

**Files:**
- Modify: `Backend/app/api/ingestion.py`
- Create: `shared/schemas/ingestiontierbcandidate.schema.json`, `ingestiontierbreviewitem.schema.json`, `ingestiontierbreviewpage.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Modify: `Backend/tests/test_ingestion_endpoints.py` (add `TestTierBReview`)
- Modify: `backend_api.md`
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, Android `Models.kt`

**Interfaces:**
- Consumes: `IngestionService.list_tier_b_review(cursor=, limit=)` (Task 2).
- Produces: `GET /api/ingestion/review/tier_b?cursor=&limit=` → `TierBReviewPage`. Generated TS: `IngestionTierBReviewPage` / `IngestionTierBReviewItem` / `IngestionTierBCandidate`.

- [ ] **Step 1: Write the failing endpoint test**

Add to `Backend/tests/test_ingestion_endpoints.py`:

```python
class TestTierBReview:
    def test_returns_page(self, client, mock_service):
        mock_service.list_tier_b_review.return_value = {
            "items": [{
                "image": {"image_id": "s1", "filename": "s1.jpg", "status": "pending", "ocr_text": "t"},
                "candidates": [{
                    "member": {"image_id": "c1", "filename": "c1.jpg", "status": "active", "ocr_text": None},
                    "distance": 0.08, "match_source": "cross_corpus"}],
                "total_candidates": 5,
            }],
            "next_cursor": "0.08|s1", "has_next": True,
        }
        r = client.get("/api/ingestion/review/tier_b")
        assert r.status_code == 200
        b = r.json()
        assert b["items"][0]["image"]["image_id"] == "s1"
        assert b["items"][0]["candidates"][0]["distance"] == 0.08
        assert b["items"][0]["total_candidates"] == 5
        assert b["has_next"] is True
        mock_service.list_tier_b_review.assert_awaited_once_with(cursor=None, limit=40)

    def test_forwards_cursor_and_limit(self, client, mock_service):
        mock_service.list_tier_b_review.return_value = {"items": [], "next_cursor": None, "has_next": False}
        client.get("/api/ingestion/review/tier_b?cursor=0.1%7Cabc&limit=10")
        mock_service.list_tier_b_review.assert_awaited_once_with(cursor="0.1|abc", limit=10)

    def test_rejects_bad_limit(self, client, mock_service):
        assert client.get("/api/ingestion/review/tier_b?limit=0").status_code == 422
        assert client.get("/api/ingestion/review/tier_b?limit=999").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py::TestTierBReview -v`
Expected: FAIL — 404 (route missing).

- [ ] **Step 3: Models + route**

`Backend/app/api/ingestion.py`, after `ClusterPage`:

```python
class TierBCandidate(BaseModel):
    member: ClusterMember
    distance: float
    match_source: Optional[str]


class TierBReviewItem(BaseModel):
    image: ClusterMember
    candidates: list[TierBCandidate]
    total_candidates: int


class TierBReviewPage(BaseModel):
    items: list[TierBReviewItem]
    next_cursor: Optional[str]
    has_next: bool


@router.get("/review/tier_b", response_model=TierBReviewPage)
async def tier_b_review(
    cursor: Optional[str] = None,
    limit: int = Query(40, ge=1, le=200),
    service: IngestionService = Depends(get_ingestion_service),
):
    return await service.list_tier_b_review(cursor=cursor, limit=limit)
```

(`Query` is already imported from the ClusterPage work.)

- [ ] **Step 4: Schema files**

`shared/schemas/ingestiontierbcandidate.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ingestiontierbcandidate.schema.json",
  "title": "IngestionTierBCandidate",
  "type": "object",
  "properties": {
    "member": { "$ref": "./ingestionclustermember.schema.json" },
    "distance": { "type": "number" },
    "match_source": { "type": ["string", "null"] }
  },
  "required": ["member", "distance", "match_source"]
}
```

`shared/schemas/ingestiontierbreviewitem.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ingestiontierbreviewitem.schema.json",
  "title": "IngestionTierBReviewItem",
  "type": "object",
  "properties": {
    "image": { "$ref": "./ingestionclustermember.schema.json" },
    "candidates": { "type": "array", "items": { "$ref": "./ingestiontierbcandidate.schema.json" } },
    "total_candidates": { "type": "integer" }
  },
  "required": ["image", "candidates", "total_candidates"]
}
```

`shared/schemas/ingestiontierbreviewpage.schema.json`:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ingestiontierbreviewpage.schema.json",
  "title": "IngestionTierBReviewPage",
  "type": "object",
  "properties": {
    "items": { "type": "array", "items": { "$ref": "./ingestiontierbreviewitem.schema.json" } },
    "next_cursor": { "type": ["string", "null"] },
    "has_next": { "type": "boolean" }
  },
  "required": ["items", "next_cursor", "has_next"]
}
```

`shared/schemas/all.schema.json` — register all three after `IngestionClusterPage`, matching the surrounding alignment:
```json
    "IngestionTierBCandidate":     { "$ref": "ingestiontierbcandidate.schema.json" },
    "IngestionTierBReviewItem":    { "$ref": "ingestiontierbreviewitem.schema.json" },
    "IngestionTierBReviewPage":    { "$ref": "ingestiontierbreviewpage.schema.json" },
```

- [ ] **Step 5: Run endpoint test to pass**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py -v`
Expected: PASS.

- [ ] **Step 6: Regenerate types — all THREE generators off `all.schema.json`**

```bash
cd Frontend && bash generate-types.sh && cd ..
python AndroidClient/scripts/generate_dtos.py
grep -n "IngestionTierBReviewPage" Frontend/memes-frontend/src/types/generated/all.d.ts   # exists, snake_case fields
```

Then the **Python DTO tree** (`Backend/app/types/generated/`, via `datamodel-codegen` — see
`documents/generation.md` for the exact command; earlier stopgap/UX branches forgot this one
and it drifted). Run the documented command from `Backend/`; expect new
`ingestiontierbcandidate.py` / `ingestiontierbreviewitem.py` / `ingestiontierbreviewpage.py`
and an updated `__init__.py`. `cd Backend && pytest -q` + `python -c "import Backend.app.main"`
must still pass. If `datamodel-codegen` isn't installed, note it in the report — do not
hand-edit the generated files.

- [ ] **Step 7: `backend_api.md`**

Add a "Tier B Review" subsection under "Ingestion" (mirror the "List Tier Clusters" format):
- `GET /api/ingestion/review/tier_b`, query `cursor` (opaque, omit for page 1) + `limit` (default 40, 1–200).
- Response `TierBReviewPage`: `items[]` of `{ image, candidates: [{member, distance, match_source}], total_candidates }`, `next_cursor`, `has_next`.
- One line: *"One item per pending image with an unresolved Tier B candidate pair, ordered by the image's tightest candidate distance. Candidates are capped at 30 (tightest); `total_candidates` is the uncapped count. Decisions go to `/api/ingestion/clusters/tier_b/resolve` as usual and settle every pair touching the decided image."*

- [ ] **Step 8: Backend regression + commit**

Run: `cd Backend && pytest -q` — PASS.
Run: `cd Backend && python -c "import Backend.app.main"` — no import errors.

```bash
git add Backend/app/api/ingestion.py shared/schemas/ Backend/tests/test_ingestion_endpoints.py \
        backend_api.md Frontend/memes-frontend/src/types/generated/all.d.ts \
        Backend/app/types/generated/ AndroidClient/
git commit -m "feat: GET /api/ingestion/review/tier_b endpoint + schemas

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 4: Frontend — API client + mock

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`

**Interfaces:**
- Consumes: `IngestionTierBReviewPage` (Task 3).
- Produces: `MemesApi.getIngestionTierBReview(cursor?: string): Promise<IngestionTierBReviewPage>`.

- [ ] **Step 1: Interface**

`MemesApi.ts` — import `IngestionTierBReviewPage` from `../types/generated/all`; add:
```ts
  getIngestionTierBReview(cursor?: string): Promise<IngestionTierBReviewPage>;
```

- [ ] **Step 2: HTTP impl**

`HttpMemesApi.ts`:
```ts
  async getIngestionTierBReview(cursor?: string): Promise<IngestionTierBReviewPage> {
    const params = new URLSearchParams({ limit: "40" })
    if (cursor) params.set("cursor", cursor)
    const res = await fetch(`${this.baseUrl}/api/ingestion/review/tier_b?${params}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch tier B review: ${res.status}`)
    return res.json()
  }
```
Import `IngestionTierBReviewPage` in its type import too.

- [ ] **Step 3: Mock**

`test/mockApi.ts`:
```ts
    getIngestionTierBReview: vi.fn().mockResolvedValue({ items: [], next_cursor: null, has_next: false }),
```

- [ ] **Step 4: Verify + commit**

Run: `cd Frontend/memes-frontend && npx tsc -b` — errors ONLY in `IngestionReviewPage.tsx`/tests are impossible yet (nothing consumes it); expect clean except any pre-existing. `npx eslint src/api src/test/mockApi.ts` — clean.

```bash
git add Frontend/memes-frontend/src/api/ Frontend/memes-frontend/src/test/mockApi.ts
git commit -m "feat: getIngestionTierBReview client

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 5: Frontend — `TierBReviewCard`

**Files:**
- Create: `Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.tsx`, `TierBReviewCard.test.tsx`

**Interfaces:**
- Consumes: `IngestionTierBReviewItem`, `IngestionClusterMember`, `Decision`, `MemesApi`, `MemberTile`.
- Produces: `TierBReviewCard` props:
  ```ts
  {
    memesApi: MemesApi
    item: IngestionTierBReviewItem
    decisions: Record<string, Decision | undefined>
    onDecide: (imageId: string, d: Decision) => void
    onSubmit: () => void
    submitting: boolean
    expanded: boolean
    onToggleExpand: () => void
    onPeek: (member: IngestionClusterMember) => void
  }
  ```

- [ ] **Step 1: Failing test**

`TierBReviewCard.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { TierBReviewCard } from './TierBReviewCard'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionTierBReviewItem } from '../../types/generated/all'

const item: IngestionTierBReviewItem = {
  image: { image_id: 's1', filename: 's1.jpg', status: 'pending', ocr_text: 'subject' },
  candidates: [
    { member: { image_id: 'c1', filename: 'c1.jpg', status: 'pending', ocr_text: null }, distance: 0.08, match_source: 'in_batch' },
    { member: { image_id: 'c2', filename: 'c2.jpg', status: 'active', ocr_text: null }, distance: 0.15, match_source: 'cross_corpus' },
  ],
  total_candidates: 2,
}

function renderCard(props: Partial<Parameters<typeof TierBReviewCard>[0]> = {}) {
  return render(
    <TierBReviewCard memesApi={makeMockApi()} item={item} decisions={{}} onDecide={vi.fn()}
      onSubmit={vi.fn()} submitting={false} expanded={false} onToggleExpand={vi.fn()} onPeek={vi.fn()} {...props} />
  )
}

describe('TierBReviewCard', () => {
  it('shows Keep/Reject on the subject and on a pending candidate, not on an active one', () => {
    renderCard()
    // subject + c1 (pending) are decidable -> 2 Reject buttons; c2 (active) is context-only
    expect(screen.getAllByRole('button', { name: /^reject$/i })).toHaveLength(2)
    expect(screen.getByText('s1.jpg')).toBeInTheDocument()
    expect(screen.getByText('c2.jpg')).toBeInTheDocument()
  })

  it('calls onDecide with the subject id and with a pending candidate id', async () => {
    const onDecide = vi.fn()
    renderCard({ onDecide })
    const rejects = screen.getAllByRole('button', { name: /^reject$/i })
    await userEvent.click(rejects[0])  // subject
    expect(onDecide).toHaveBeenCalledWith('s1', 'reject')
    await userEvent.click(rejects[1])  // c1
    expect(onDecide).toHaveBeenCalledWith('c1', 'reject')
  })

  it('shows a candidate distance/source line', () => {
    renderCard()
    expect(screen.getByText(/0\.080 · in_batch/)).toBeInTheDocument()
  })

  it('shows "Showing K of N" when total_candidates exceeds the returned list', () => {
    renderCard({ item: { ...item, total_candidates: 240 } })
    expect(screen.getByText(/showing 2 of 240/i)).toBeInTheDocument()
  })

  it('per-card submit is disabled until a decidable image has a decision', () => {
    const { rerender } = renderCard()
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeDisabled()
    rerender(<TierBReviewCard memesApi={makeMockApi()} item={item} decisions={{ s1: 'keep' }}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false} expanded={false}
      onToggleExpand={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeEnabled()
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Frontend/memes-frontend && npx vitest run src/components/ingestion/TierBReviewCard.test.tsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```tsx
import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember, IngestionTierBReviewItem } from "../../types/generated/all"
import type { Decision } from "./types"
import { MemberTile } from "./MemberTile"

type Props = {
  memesApi: MemesApi
  item: IngestionTierBReviewItem
  decisions: Record<string, Decision | undefined>
  onDecide: (imageId: string, d: Decision) => void
  onSubmit: () => void
  submitting: boolean
  expanded: boolean
  onToggleExpand: () => void
  onPeek: (member: IngestionClusterMember) => void
}

const COLLAPSED_COUNT = 8

export function TierBReviewCard({
  memesApi, item, decisions, onDecide, onSubmit, submitting, expanded, onToggleExpand, onPeek,
}: Props) {
  const decidable = [
    item.image.image_id,
    ...item.candidates.filter((c) => c.member.status === "pending").map((c) => c.member.image_id),
  ]
  const hasDecision = decidable.some((id) => decisions[id] !== undefined)
  const shown = expanded ? item.candidates : item.candidates.slice(0, COLLAPSED_COUNT)
  const hiddenInList = item.candidates.length - COLLAPSED_COUNT
  const cappedOff = item.total_candidates - item.candidates.length

  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-4">
      <div className="mb-3 border-b pb-3">
        <MemberTile
          memesApi={memesApi} member={item.image} edgeSummary={null}
          decision={decisions[item.image.image_id]}
          onDecide={(d) => onDecide(item.image.image_id, d)}
          onPeek={() => onPeek(item.image)}
        />
      </div>
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(300px,1fr))]">
        {shown.map((c) => (
          <MemberTile
            key={c.member.image_id}
            memesApi={memesApi}
            member={c.member}
            edgeSummary={`${c.distance.toFixed(3)} · ${c.match_source ?? "?"}`}
            decision={c.member.status === "pending" ? decisions[c.member.image_id] : undefined}
            onDecide={
              c.member.status === "pending"
                ? (d) => onDecide(c.member.image_id, d)
                : () => {}
            }
            onPeek={() => onPeek(c.member)}
          />
        ))}
      </div>
      {cappedOff > 0 && (
        <p className="mt-2 text-xs text-gray-500">
          Showing {item.candidates.length} of {item.total_candidates} candidates.
        </p>
      )}
      <div className="mt-3 flex items-center gap-3">
        <button
          className="text-sm rounded bg-blue-600 text-white px-3 py-1 transition-colors active:scale-[.97] disabled:opacity-40"
          disabled={!hasDecision || submitting}
          onClick={onSubmit}
        >
          {submitting ? "Submitting…" : "Submit decisions"}
        </button>
        {hiddenInList > 0 && (
          <button className="text-sm rounded bg-gray-100 hover:bg-gray-200 px-3 py-1" onClick={onToggleExpand}>
            {expanded ? "Show fewer" : `Show ${hiddenInList} more`}
          </button>
        )}
      </div>
    </div>
  )
}
```

`MemberTile` renders Keep/Reject only when `member.status === "pending"` (existing behavior), so an `active` candidate is automatically context-only — the `onDecide` no-op is a belt-and-braces guard.

- [ ] **Step 4: Run to verify pass**

Run: `cd Frontend/memes-frontend && npx vitest run src/components/ingestion/TierBReviewCard.test.tsx`
Expected: PASS (5).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.tsx Frontend/memes-frontend/src/components/ingestion/TierBReviewCard.test.tsx
git commit -m "feat: TierBReviewCard component

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 6: Frontend — `IngestionReviewPage` tier branch

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`

**Interfaces:**
- Consumes: `getIngestionTierBReview` (Task 4), `TierBReviewCard` (Task 5).
- Produces: the finished page. No new exports.

**Approach:** add a parallel `tierBItems` state and branch `load`/`loadMore`/render on `tier`; generalise `decidedPendingIn` and `isFullyResolved` over `IngestionCluster | IngestionTierBReviewItem`. Everything else (`decisions`, `submitting` keyed by unit object, the fixed action bar, `runSubmit`'s optimistic machinery, `resolvedStatus` flip, reload-when-empty) is already `image_id`/unit-object based and needs only the two helpers to understand the new shape.

- [ ] **Step 1: Rewrite the affected page tests**

In `IngestionReviewPage.test.tsx`, add a `tier_b` describe block. Reuse the `vi.mock('react-virtuoso')` + `capturedProps` pattern already in the file.

```tsx
const tierBStatus = { ...runStatus, stage: 'tier_b_review' }

function tbItem(sid: string, candIds: [string, 'pending' | 'active'][], dist = 0.08): IngestionTierBReviewItem {
  return {
    image: { image_id: sid, filename: `${sid}.jpg`, status: 'pending', ocr_text: null },
    candidates: candIds.map(([cid, st], i) => ({
      member: { image_id: cid, filename: `${cid}.jpg`, status: st, ocr_text: null },
      distance: dist + i * 0.01, match_source: 'in_batch',
    })),
    total_candidates: candIds.length,
  }
}
const tbPage = (items: IngestionTierBReviewItem[], next: string | null = null) =>
  ({ items, next_cursor: next, has_next: next !== null })

describe('IngestionReviewPage — tier B', () => {
  it('renders per-image cards from the tier-b review endpoint, not clusters', async () => {
    const getIngestionTierBReview = vi.fn().mockResolvedValue(tbPage([tbItem('s1', [['c1', 'active']])]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(tierBStatus),
      getIngestionTierBReview,
    })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText('s1.jpg')).toBeInTheDocument()
    expect(screen.getByText('c1.jpg')).toBeInTheDocument()
    expect(getIngestionTierBReview).toHaveBeenCalledWith(undefined)
  })

  it('removes a card once its subject is decided and submitted', async () => {
    const getIngestionTierBReview = vi.fn().mockResolvedValue(tbPage([tbItem('s1', [['c1', 'active']]), tbItem('s2', [['c3', 'active']])]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['s1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(tierBStatus),
      getIngestionTierBReview, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /^reject$/i })[0])  // s1 subject
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledWith('tier_b', [{ image_id: 's1', decision: 'reject' }]))
    await waitFor(() => expect(screen.queryByText('s1.jpg')).toBeNull())
    expect(screen.getByText('s2.jpg')).toBeInTheDocument()
  })

  it('a decision on an in-batch candidate shown on two cards reflects on both', async () => {
    // s1's card lists c1 (pending); c1 also has its own card
    const getIngestionTierBReview = vi.fn().mockResolvedValue(
      tbPage([tbItem('s1', [['c1', 'pending']]), tbItem('c1', [['x9', 'active']])]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(tierBStatus),
      getIngestionTierBReview,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('s1.jpg')
    // reject c1 from s1's card (the 2nd reject button: [0]=s1 subject, [1]=c1 candidate, [2]=c1 subject-card)
    const rejects = screen.getAllByRole('button', { name: /^reject$/i })
    await userEvent.click(rejects[1])
    // both c1 tiles now show the reject highlight
    await waitFor(() => {
      const highlighted = screen.getAllByRole('button', { name: /^reject$/i }).filter(b => b.className.includes('bg-red-600'))
      expect(highlighted.length).toBe(2)
    })
  })
})
```

Add the imports (`IngestionTierBReviewItem`) at the top.

- [ ] **Step 2: Run to verify failure**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: the tier-B block FAILS (page still only knows clusters).

- [ ] **Step 3: Add tier-B state + load branch**

In `IngestionReviewPage.tsx`:

- Import `TierBReviewCard` and the `IngestionTierBReviewItem` type.
- Add state: `const [tierBItems, setTierBItems] = useState<IngestionTierBReviewItem[]>([])`.
- Type alias near the top: `type ReviewUnit = IngestionCluster | IngestionTierBReviewItem`.
- Helper: `const isTierBItem = (u: ReviewUnit): u is IngestionTierBReviewItem => "image" in u`.
- In `load()`'s `.then` chain, branch on tier:
  ```ts
  const t = s ? tierForStage(s.stage) : null
  if (t === "tier_b") {
    const pageResult = await memesApi.getIngestionTierBReview(undefined)
    setTierBItems(pageResult.items); setClusters([])
    setNextCursor(pageResult.next_cursor); setHasNext(pageResult.has_next)
  } else if (t) {
    const pageResult = await memesApi.getIngestionClusters(t, undefined)
    setClusters(pageResult.items); setTierBItems([])
    setNextCursor(pageResult.next_cursor); setHasNext(pageResult.has_next)
  } else {
    setClusters([]); setTierBItems([]); setNextCursor(null); setHasNext(false)
  }
  ```
  (Restructure `load` so the awaits are sequential — it's currently a `.then` chain; convert to `async`/`await` inside the `useCallback` body, keeping the `catch`/`finally`.)
- `loadMore`: same branch — `getIngestionTierBReview(nextCursor)` appends to `tierBItems`, else `getIngestionClusters` appends to `clusters`.
- The `[tier]` effect that clears `decisions` also clears `tierBItems`/`clusters` for the tier not in use (harmless; keeps state tidy).

- [ ] **Step 4: Generalise the submit helpers**

```ts
function decidedPendingIn(unit: ReviewUnit): { image_id: string; decision: Decision }[] {
  const out: { image_id: string; decision: Decision }[] = []
  if (isTierBItem(unit)) {
    const push = (id: string) => {
      const d = decisions[id]
      if (d !== undefined) out.push({ image_id: id, decision: d })
    }
    push(unit.image.image_id)
    for (const c of unit.candidates) if (c.member.status === "pending") push(c.member.image_id)
    return out
  }
  for (const m of unit.members) {
    if (m.status !== "pending") continue
    const d = decisions[m.image_id]
    if (d !== undefined) out.push({ image_id: m.image_id, decision: d })
  }
  return out
}

function isFullyResolved(unit: ReviewUnit): boolean {
  if (isTierBItem(unit)) return decisions[unit.image.image_id] !== undefined
  const pending = unit.members.filter((m) => m.status === "pending")
  return pending.length > 0 && pending.every((m) => decisions[m.image_id] !== undefined)
}
```

`runSubmit(which, toSubmit)` — change `toSubmit: { cluster: IngestionCluster; index: number }[]` to `{ unit: ReviewUnit; index: number }[]`; rename `cluster` → `unit` throughout its body; `removedSet` is a `Set<ReviewUnit>`. The optimistic `setClusters`/`setTierBItems` on removal/re-insert/rollback must act on **whichever list is active** — introduce:
```ts
const activeList = tierBItems.length > 0 ? "tierB" : "clusters"
const setActive = activeList === "tierB" ? setTierBItems : setClusters
```
and use `setActive` in `runSubmit` instead of `setClusters` directly (it's only ever one list at a time). `survivingCount` reads `next.length` from that updater as now.

The `resolvedStatus` flip: for a tier-B unit, map over `unit.candidates` flipping `c.member.status` when `c.member.image_id` is in `resolvedStatus`. Add that branch alongside the existing cluster branch.

`submitCluster`/`submitAll` → operate on `activeList`'s items: `submitAll` builds `toSubmit` from `(tierBItems.length ? tierBItems : clusters).map((unit, index) => ({ unit, index }))`. Rename `submitCluster(cluster, index)` → `submitUnit(unit, index)`.

`clustersWithPendingCount` / `allPendingCount` memo — iterate `(tierBItems.length ? tierBItems : clusters)`.

- [ ] **Step 5: Branch the render**

Replace the single `<Virtuoso data={clusters} ...>` with a tier branch:

```tsx
{tier === "tier_b" ? (
  <Virtuoso
    useWindowScroll
    data={tierBItems}
    endReached={() => { void loadMore() }}
    increaseViewportBy={{ top: 600, bottom: 1600 }}
    itemContent={(index, item) => (
      <TierBReviewCard
        memesApi={memesApi}
        item={item}
        decisions={decisions}
        onDecide={setDecision}
        onSubmit={() => void submitUnit(item, index)}
        submitting={submitting === item || submitting === "all"}
        expanded={expandedClusters.has(item as unknown as IngestionCluster)}
        onToggleExpand={() => toggleExpand(item as unknown as IngestionCluster)}
        onPeek={setPeek}
      />
    )}
  />
) : (
  /* existing <Virtuoso data={clusters} ...> block unchanged */
)}
```

`toggleExpand`/`expandedClusters` are keyed by object identity — generalise their type to `ReviewUnit` (rename to `expandedUnits`/`toggleExpand(unit: ReviewUnit)`) so the casts above aren't needed; a one-type-param change.

The empty-state / "Load more" / no-run / OCR-prepass messages: gate the tier-B empty message on `tier === "tier_b" && tierBItems.length === 0 && !hasNext`.

- [ ] **Step 6: Run the page tests**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: PASS — tier-A tests unchanged, tier-B block green. Iterate on the component, not the tests.

- [ ] **Step 7: Full frontend gate**

```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/ && npx vitest run
```
Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "feat: IngestionReviewPage routes tier B to per-image review

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 7: Integration test for the decision cascade + verification + spec status

**Files:**
- Modify: `tests/integration/test_ingestion_tier_b_review.py` (add the resolve-cascade cases)
- Modify: `docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md`

- [ ] **Step 1: Add the cascade integration tests**

```python
from unittest.mock import patch
from Backend.app.repositories.ingestion_repository import IngestionRepository
from Backend.app.services.ingestion_service import IngestionService


@pytest.mark.asyncio(loop_scope="session")
async def test_rejecting_a_subject_drops_it_and_prunes_it_from_other_cards(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    active = await _img(db_session, "active", bid)
    await _pair(db_session, p1, p2, 0.09)       # p1<->p2
    await _pair(db_session, p2, active, 0.20)   # p2 also has an active candidate

    service = IngestionService(IngestionRepository(db_session))
    with patch("Backend.app.services.ingestion_service.image_store.move_to_rejected"):
        await service.resolve("tier_b", [{"image_id": p1, "decision": "reject"}])

    page = await service.list_tier_b_review(batch_id=bid)
    sids = [it["image"]["image_id"] for it in page["items"]]
    assert str(p1) not in sids                  # p1 rejected -> not a subject
    p2_item = next(it for it in page["items"] if it["image"]["image_id"] == str(p2))
    cand_ids = {c["member"]["image_id"] for c in p2_item["candidates"]}
    assert str(p1) not in cand_ids              # p1<->p2 pair excluded (rejected side)
    assert str(active) in cand_ids              # p2's other candidate remains

    blocked = await service.repo.get_blocked_pending_ids(bid, tier_a_high=0.05, tier_b_high=0.30)
    assert p1 not in blocked and p2 in blocked


@pytest.mark.asyncio(loop_scope="session")
async def test_keeping_a_subject_settles_its_pairs_and_drops_a_now-empty-other(db_session):
    bid = await _run(db_session)
    p1 = await _img(db_session, "pending", bid)
    p2 = await _img(db_session, "pending", bid)
    await _pair(db_session, p1, p2, 0.09)       # p2's only pair is to p1

    service = IngestionService(IngestionRepository(db_session))
    await service.resolve("tier_b", [{"image_id": p1, "decision": "keep"}])

    page = await service.list_tier_b_review(batch_id=bid)
    assert page["items"] == []                  # p1 kept -> its pair to p2 reviewed -> p2 has none
    blocked = await service.repo.get_blocked_pending_ids(bid, tier_a_high=0.05, tier_b_high=0.30)
    assert p1 not in blocked and p2 not in blocked
```

(Fix the function name — no hyphen in Python identifiers: `test_keeping_a_subject_settles_its_pairs_and_drops_a_now_empty_other`.)

- [ ] **Step 2: Run the integration file**

Run: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ingestion_tier_b_review.py -v`
Expected: PASS (all cases).

- [ ] **Step 3: Full backend + integration sweep**

```bash
cd Backend && pytest -q
```
Expected: PASS.

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q -k "ingestion or clusterize"
```
Expected: PASS — including the existing cluster/split integration tests (tier A path untouched).

- [ ] **Step 4: Full frontend gate + drift**

```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/ && npx vitest run
cd .. && cd Frontend && bash generate-types.sh && cd .. && python AndroidClient/scripts/generate_dtos.py
git diff --stat   # expect empty (Task 3 committed the regenerated files)
```

- [ ] **Step 5: Manual smoke (if `general` dev servers are up)**

`/ingestion` on `general` (currently `tier_b_review`): the page loads within a few seconds; each card is one pending image + up to ~30 candidate tiles; reject/keep the subject → the card leaves the list on submit; the "Submit all" bar aggregates across cards; the run's blocked count drops as cards are resolved. Tier A (switch a run there, or trust its unchanged tests) is unaffected.

If no `general` run is on tier B, note it and rely on the integration tests.

- [ ] **Step 6: Spec status + commit**

`docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md`: `status: approved` → `status: done`; add `Plan: docs/superpowers/plans/2026-09-10-ingestion-tier-b-per-image-review.md`.

```bash
git add tests/integration/test_ingestion_tier_b_review.py docs/superpowers/specs/2026-09-10-ingestion-tier-b-per-image-review-design.md
git commit -m "test: Tier B review decision cascade; mark spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

- [ ] **Step 7: Whole-branch review**

Invoke `superpowers:requesting-code-review` for the branch against the spec. One iteration; fix action points; post the final summary. Pay attention to: the raw-SQL repo method (injection surface — all values are asyncpg params, verify), the `runSubmit` generalisation (did any tier-A optimistic path regress?), and the cross-card decision consistency.

---

## Self-Review (completed during planning)

**Spec coverage:**
- §1 repo (Query A rank+page, Query B candidates, HAVING cursor) → Task 1.
- §2 service (`list_tier_b_review`, `CANDIDATE_CAP`, OCR, cursor) → Task 2.
- §3 API (endpoint, models, 3 schemas, regen, `backend_api.md`) → Task 3.
- §4 frontend client + types + mock → Task 4.
- §5 page tier branch + generalised helpers → Task 6.
- §6 `TierBReviewCard` (subject pinned, pending vs active candidates, cap notice, distance line) → Task 5.
- §7 optimistic submit generalisation (`decidedPendingIn`, `isFullyResolved`, `resolvedStatus` flip) → Task 6 Step 4.
- Invariants: #1 (subject set) → Task 1 tests; #2 (decided once) → Task 7 cascade tests + Task 6 cross-card test; #3 (`get_blocked_pending_ids`) → Task 7; #4 (tier A unchanged) → Task 6 keeps the cluster `<Virtuoso>` block, Task 7 Step 3 runs the cluster integration tests; #5 (cursor) → Task 1/2 tests.
- Non-goals respected: `resolve`/`mark_reviewed`/`reject_image`/schema/bands untouched; the cluster tier-b endpoint left callable.

**Placeholder scan:** none. Every code step has literal code; every test step names the command + expected result. Task 6 is the largest and is broken into 5 numbered sub-steps with the exact edits.

**Type consistency:** `list_tier_b_review_page(batch_id, low, high, cursor, limit) -> (subjects, candidates)` — same row field names (`subject_id`, `min_distance`, `total_candidates`, `cand_id`, `cand_status`, `match_source`) in the SQL `SELECT`, the repo return, the service consumer, and the mock `SimpleNamespace` builders in Task 2's test. `TierBReviewItem` shape (`image` / `candidates: [{member, distance, match_source}]` / `total_candidates`) identical across service (Task 2), Pydantic (Task 3), schema (Task 3), `TierBReviewCard` (Task 5), page tests (Task 6). `CANDIDATE_CAP` imported from `ingestion_service` in Task 2's test. `getIngestionTierBReview(cursor?) -> IngestionTierBReviewPage` consistent Tasks 4/6. `_encode_cursor`/`_decode_cursor` reused unchanged.

**`CLUSTER_MEMBER_CAP` keep-vs-remove decision (per the design spec's Rollout section):** kept, not
removed. `CLUSTER_MEMBER_CAP`/`_members_by_tightest_edge` in `Backend/app/services/ingestion_service.py`
are now dead code for Tier B (`list_clusters` is no longer called for that tier — the page routes
to `list_tier_b_review` instead), but stay live as Tier A's safety net for a rare oversized
union-find component, where they're harmless and still needed (Tier A has no per-image review
path to fall back on). The capped-cluster UI/doc wording (`ClusterRow.tsx`, `MemberTile.tsx`,
the `CLUSTER_MEMBER_CAP` comment, `backend_api.md`) was corrected in this branch's final-review
fix wave to stop claiming a future "per-image review is coming" — that per-image path shipped,
this branch, Tier-B-only, so the read-only cap state is now Tier A's permanent behavior, not a
stopgap awaiting removal.
