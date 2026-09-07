# Ingestion Review UX Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `/ingestion` Tier A/B review page fast and low-click — whole images and correctly-ordered OCR text visible without a modal, a virtualized paginated list, a docked full-size preview, a fixed bulk-submit bar, and optimistic cluster removal.

**Architecture:** Backend adds cursor pagination to `GET /api/ingestion/clusters/{tier}` (full union-find still computed per call; clusters sorted by tightest edge; opaque `distance|uuid` cursor) and fixes `get_ocr_texts` to gate on `lang_score` and order by language plausibility so Russian text leads instead of EN/ES transliteration noise. Frontend rewrites `IngestionReviewPage` around `<Virtuoso useWindowScroll>` with append-on-`endReached`, a `ClusterRow`/`MemberTile` component pair showing uncropped images + full OCR, a `position: fixed` docked preview pane on hover, a `position: fixed` action bar, and optimistic removal of resolved clusters.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), React 19 + TypeScript + Tailwind v4 + react-virtuoso + Vitest (frontend), hand-written JSON Schema in `shared/schemas/` → generated TS (`json-schema-to-typescript`) and Kotlin DTOs.

**Spec:** `docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md`

## Global Constraints

- **Never combine `Backend/tests/` with other pytest roots in one invocation** — separate `pytest.ini` `asyncio_mode`. Run `cd Backend && pytest ...` on its own.
- **Windows:** prefix interactive batch/uvicorn commands per CLAUDE.md (`set WATCHFILES_FORCE_POLLING=1` for uvicorn). Not relevant to pytest/vitest.
- **Repository layer is config-agnostic** — repositories take threshold values as parameters; the service reads `settings.*` and passes them down. Do not `import config.settings` inside `Backend/app/repositories/`.
- **Do not add backend dependencies.** Specifically: do not import `rules.lang_plausibility` (pulls in `wordfreq`), do not add Pillow. `EMBEDDING_DIM = 512`.
- **`backend_api.md` is authoritative** — update it in the same task that changes an endpoint.
- **Generated files must not drift** — CI diffs `Frontend/memes-frontend/src/types/generated/` and the Android DTOs. Regenerate and commit in the same task as the schema change.
- **Repositories must not call `session.commit()`** — `get_async_db` owns that (exception: the existing `IngestionRepository.commit()` used only by `resolve()`).
- Frontend pre-commit gate: `tsc -b`, `eslint src/` (0 warnings), `vitest run` — all three must pass.
- Cursor format: `f"{min_distance!r}|{min_image_id}"` (exact float round-trip via `repr()`/`float()` — a `:.6f` truncation can silently skip a page-boundary cluster). Cluster sort key: `(min_distance, str(min_image_id))` ascending. Malformed/blank cursor ⇒ start from beginning, never 4xx.
- `limit` default `40`, `Query(40, ge=1, le=200)`.

---

## File Structure

**Backend — modify:**
- `Backend/app/repositories/ingestion_repository.py` — `get_ocr_texts` gains `confidence_min`, `lang_score_min` params; selects `lang_score`; orders by `lang_score DESC NULLS LAST, confidence DESC`; dedupes block text.
- `Backend/app/services/ingestion_service.py` — `list_clusters` gains `cursor`, `limit`; sorts clusters by tightest edge; encodes/decodes the cursor; passes OCR thresholds from `settings.OCR`.
- `Backend/app/api/ingestion.py` — new `ClusterPage` model; `list_clusters` route gains `cursor`/`limit` query params, `response_model=ClusterPage`.

**Backend — create:**
- `Backend/tests/test_ingestion_repository.py` — unit tests for `get_ocr_texts` with a mocked session.

**Shared — create / modify:**
- `shared/schemas/ingestionclusterpage.schema.json` — new.
- `shared/schemas/all.schema.json` — register `IngestionClusterPage`.

**Generated (regenerated, not hand-edited):**
- `Frontend/memes-frontend/src/types/generated/all.d.ts`
- `AndroidClient/` DTOs (whatever `scripts/generate_dtos.py` writes).

**Docs — modify:**
- `backend_api.md` — "List Clusters" section.

**Frontend — modify:**
- `Frontend/memes-frontend/src/api/MemesApi.ts` — `getIngestionClusters` signature/return.
- `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts` — same, `?cursor=&limit=`.
- `Frontend/memes-frontend/src/test/mockApi.ts` — page-shaped default.
- `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx` — the bulk of the work.
- `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx` — rewrite for new shape/layout.

**Frontend — create:**
- `Frontend/memes-frontend/src/pages/ingestion/ClusterRow.tsx` — one cluster card (member strip + per-cluster submit).
- `Frontend/memes-frontend/src/pages/ingestion/MemberTile.tsx` — one member (uncropped image, full OCR, keep/reject, hover wiring).
- `Frontend/memes-frontend/src/pages/ingestion/DockedPreview.tsx` — fixed right-side full-size pane.
- `Frontend/memes-frontend/src/pages/ingestion/types.ts` — shared `Decision` type + small helpers if needed.

(If the repo convention is flat `components/`, put the three components there instead — check where `MemesDuplicatesList.tsx` and friends live; they're in `src/components/`. Prefer `src/components/ingestion/` to match. Adjust import paths accordingly and keep it consistent.)

---

## Task 1: Backend — `get_ocr_texts` lang_score gate, ordering, dedup

**Files:**
- Modify: `Backend/app/repositories/ingestion_repository.py` (`get_ocr_texts`, ~line 86)
- Create: `Backend/tests/test_ingestion_repository.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `IngestionRepository.get_ocr_texts(image_ids, confidence_min: float, lang_score_min: float) -> dict[UUID, str]` — concatenated, deduped, language-plausibility-ordered OCR text per image id. Callers must now pass both thresholds.

- [ ] **Step 1: Write the failing test**

Create `Backend/tests/test_ingestion_repository.py`:

```python
"""Unit tests for IngestionRepository.get_ocr_texts — mocked session, no DB."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from Backend.app.repositories.ingestion_repository import IngestionRepository


def _rows(*tuples):
    """Each tuple: (image_id, text, confidence, lang_score). Mimics session.execute(...).all()."""
    result = SimpleNamespace(all=lambda: list(tuples))
    return result


@pytest.fixture
def repo():
    session = AsyncMock()
    return IngestionRepository(session), session


class TestGetOcrTexts:
    async def test_empty_ids_returns_empty_without_query(self, repo):
        r, session = repo
        assert await r.get_ocr_texts([], 0.4, 0.3) == {}
        session.execute.assert_not_awaited()

    async def test_drops_low_confidence_and_low_lang_score_blocks(self, repo):
        r, session = repo
        img = uuid.uuid4()
        session.execute.return_value = _rows(
            (img, "Не смешно", 0.85, 1.0),        # keep
            (img, "Haka3aha 40 cpepbl", 0.55, 0.2),  # drop: lang_score < 0.3
            (img, "blur", 0.2, 0.9),               # drop: confidence < 0.4
        )
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out == {img: "Не смешно"}

    async def test_orders_by_lang_score_desc_then_confidence_desc(self, repo):
        r, session = repo
        img = uuid.uuid4()
        # Deliberately supplied out of desired order; method must not reorder in Python if the
        # query already ORDER BYs — but the test asserts the *output* order regardless.
        session.execute.return_value = _rows(
            (img, "second", 0.90, 0.40),
            (img, "first", 0.70, 0.95),
            (img, "third", 0.99, 0.35),
        )
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out[img] == "first second third"

    async def test_dedupes_identical_block_text(self, repo):
        r, session = repo
        img = uuid.uuid4()
        session.execute.return_value = _rows(
            (img, "ВЕРНУТЬ МОНАРХИЮ", 0.85, 1.0),
            (img, "ВЕРНУТЬ МОНАРХИЮ", 0.85, 1.0),
            (img, "ага", 0.85, 1.0),
        )
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out[img] == "ВЕРНУТЬ МОНАРХИЮ ага"

    async def test_none_lang_score_is_kept(self, repo):
        r, session = repo
        img = uuid.uuid4()
        session.execute.return_value = _rows((img, "short", 0.85, None))
        out = await r.get_ocr_texts([img], 0.4, 0.3)
        assert out == {img: "short"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd Backend && pytest tests/test_ingestion_repository.py -v`
Expected: FAIL — `get_ocr_texts()` currently takes only `image_ids` (TypeError on the extra positional args) / no ordering / no dedup.

- [ ] **Step 3: Implement**

In `Backend/app/repositories/ingestion_repository.py`, replace `get_ocr_texts`:

```python
    async def get_ocr_texts(self, image_ids, confidence_min: float, lang_score_min: float) -> dict:
        """Concatenated OCR text per image id for the review UI. Drops blocks below either
        threshold, orders the survivors most-language-plausible first (so e.g. Russian text
        leads instead of the EN/ES EasyOCR readers' Latin transliteration noise), and dedupes
        identical block text. Thresholds are passed in by the service — this layer stays
        config-agnostic (mirrors get_blocked_pending_ids)."""
        if not image_ids:
            return {}
        result = await self.session.execute(
            select(OCRText.image_id, OCRText.text, OCRText.confidence, OCRText.lang_score)
            .where(OCRText.image_id.in_(image_ids))
            .order_by(
                OCRText.lang_score.desc().nullslast(),
                OCRText.confidence.desc().nullslast(),
            )
        )
        by_image: dict = {}
        seen: dict = {}
        for image_id, text, confidence, lang_score in result.all():
            if confidence is not None and confidence < confidence_min:
                continue
            if lang_score is not None and lang_score < lang_score_min:
                continue
            block = (text or "").strip()
            if not block:
                continue
            seen_for_image = seen.setdefault(image_id, set())
            if block in seen_for_image:
                continue
            seen_for_image.add(block)
            by_image.setdefault(image_id, []).append(block)
        return {image_id: " ".join(parts) for image_id, parts in by_image.items()}
```

Confirm `nullslast` is importable — SQLAlchemy exposes it as a method on column expressions (`.desc().nullslast()`) in the version this repo pins; if the linter/runtime complains, use `from sqlalchemy import nullslast` and wrap: `nullslast(OCRText.lang_score.desc())`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd Backend && pytest tests/test_ingestion_repository.py -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/ingestion_repository.py Backend/tests/test_ingestion_repository.py
git commit -m "fix: order and lang_score-gate ingestion OCR text so Russian leads

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 2: Backend — paginate `list_clusters` (service)

**Files:**
- Modify: `Backend/app/services/ingestion_service.py` (`list_clusters`, ~line 60; imports)
- Modify: `Backend/tests/test_ingestion_service.py` (add a `TestListClustersPagination` class)

**Interfaces:**
- Consumes: `IngestionRepository.get_ocr_texts(image_ids, confidence_min, lang_score_min)` from Task 1.
- Produces: `IngestionService.list_clusters(tier, batch_id=None, cursor: str | None = None, limit: int = 40) -> dict` returning `{"items": list[dict], "next_cursor": str | None, "has_next": bool}`. Each `items[i]` is the existing `{"members": [...], "edges": [...]}` dict, unchanged. Also module-level helpers `_encode_cursor(min_distance: float, min_image_id: str) -> str` and `_decode_cursor(cursor: str | None) -> tuple[float, str] | None`.

- [ ] **Step 1: Write the failing tests**

Add to `Backend/tests/test_ingestion_service.py`:

```python
class TestListClustersPagination:
    """list_clusters builds the full union-find, sorts clusters by tightest edge, and returns
    one cursor-delimited page. mock_repo returns raw tmp_duplicates rows."""

    def _row(self, id1, id2, distance, status1="pending", status2="pending"):
        # matches get_tier_candidate_rows' tuple shape:
        # (image_id1, filename1, status1, image_id2, filename2, status2, distance, match_source)
        return (id1, f"{id1}.jpg", status1, id2, f"{id2}.jpg", status2, distance, "clip")

    async def _setup(self, service, mock_repo, rows):
        mock_repo.get_active_run.return_value = SimpleNamespace(run_id=uuid.uuid4())
        mock_repo.get_tier_candidate_rows.return_value = rows
        mock_repo.get_ocr_texts.return_value = {}

    async def test_orders_clusters_by_tightest_edge_and_paginates(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        b1, b2 = "00000000-0000-0000-0000-0000000000b1", "00000000-0000-0000-0000-0000000000b2"
        c1, c2 = "00000000-0000-0000-0000-0000000000c1", "00000000-0000-0000-0000-0000000000c2"
        rows = [
            self._row(b1, b2, 0.20),
            self._row(a1, a2, 0.05),   # tightest -> first
            self._row(c1, c2, 0.30),
        ]
        await self._setup(service, mock_repo, rows)

        page1 = await service.list_clusters("tier_b", limit=2)
        assert [e["distance"] for c in page1["items"] for e in c["edges"]] == [0.05, 0.20]
        assert page1["has_next"] is True
        assert page1["next_cursor"] is not None

        page2 = await service.list_clusters("tier_b", cursor=page1["next_cursor"], limit=2)
        assert [e["distance"] for c in page2["items"] for e in c["edges"]] == [0.30]
        assert page2["has_next"] is False
        assert page2["next_cursor"] is None

    async def test_blank_and_malformed_cursor_start_from_beginning(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        await self._setup(service, mock_repo, [self._row(a1, a2, 0.05)])
        for bad in ("", "   ", "not-a-cursor", "abc|def", "0.1|"):
            page = await service.list_clusters("tier_b", cursor=bad, limit=10)
            assert len(page["items"]) == 1

    async def test_empty_queue(self, service, mock_repo):
        await self._setup(service, mock_repo, [])
        page = await service.list_clusters("tier_b", limit=10)
        assert page == {"items": [], "next_cursor": None, "has_next": False}

    async def test_passes_ocr_thresholds_from_settings(self, service, mock_repo):
        a1, a2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
        await self._setup(service, mock_repo, [self._row(a1, a2, 0.05)])
        await service.list_clusters("tier_b", limit=10)
        _, kwargs = mock_repo.get_ocr_texts.call_args
        args = mock_repo.get_ocr_texts.call_args.args
        # confidence_min, lang_score_min passed positionally after image_ids
        assert args[1:] == (0.4, 0.3) or (kwargs.get("confidence_min"), kwargs.get("lang_score_min")) == (0.4, 0.3)
```

Add `from types import SimpleNamespace` to the test file's imports if not present.

- [ ] **Step 2: Run to verify failure**

Run: `cd Backend && pytest tests/test_ingestion_service.py::TestListClustersPagination -v`
Expected: FAIL — `list_clusters` has no `cursor`/`limit` params and returns a bare list.

- [ ] **Step 3: Implement**

In `Backend/app/services/ingestion_service.py`:

Add near the top (after existing imports):

```python
CURSOR_SEP = "|"


def _encode_cursor(min_distance: float, min_image_id: str) -> str:
    # repr() of a float round-trips exactly through float() — a :.Nf truncation could make two
    # near-equal cluster distances collide and silently drop the later cluster from every page.
    return f"{min_distance!r}{CURSOR_SEP}{min_image_id}"


def _decode_cursor(cursor: str | None) -> tuple[float, str] | None:
    """(min_distance, min_image_id) or None for anything unparseable — a stale bookmark just
    restarts the queue, never an error."""
    if not cursor or CURSOR_SEP not in cursor:
        return None
    head, _, tail = cursor.partition(CURSOR_SEP)
    tail = tail.strip()
    if not tail:
        return None
    try:
        return (float(head), tail)
    except ValueError:
        return None
```

Rewrite `list_clusters` — keep the existing union-find body that produces the `clusters` list of `{"members", "edges"}` dicts, then replace the final `return clusters` with sorting + slicing:

```python
    async def list_clusters(
        self, tier: str, batch_id: Optional[UUID] = None,
        cursor: Optional[str] = None, limit: int = 40,
    ) -> dict:
        resolved_id = await self._resolve_batch_id(batch_id)
        low, high = _tier_band(tier)
        rows = await self.repo.get_tier_candidate_rows(resolved_id, tier, low, high)

        # ... unchanged union-find / member_info / edges building ...

        ocr_texts = await self.repo.get_ocr_texts(
            member_uuids, settings.OCR.CONFIDENCE_MIN, settings.OCR.LANG_SCORE_MIN,
        )
        for uid, info in member_info.items():
            info["ocr_text"] = ocr_texts.get(uid)

        clusters = []
        for root in uf.list_clusters():
            cluster_members = uf.get_cluster(root)
            member_ids = {str(m) for m in cluster_members}
            cluster_edges = [
                e for e in edges
                if e["image_id1"] in member_ids and e["image_id2"] in member_ids
            ]
            min_distance = min((e["distance"] for e in cluster_edges), default=1.0)
            min_image_id = min(member_ids)
            clusters.append({
                "members": [member_info[m] for m in cluster_members],
                "edges": cluster_edges,
                "_sort_key": (min_distance, min_image_id),
            })

        clusters.sort(key=lambda c: c["_sort_key"])

        decoded = _decode_cursor(cursor)
        if decoded is not None:
            clusters = [c for c in clusters if c["_sort_key"] > decoded]

        page = clusters[:limit]
        has_next = len(clusters) > limit
        next_cursor = _encode_cursor(*page[-1]["_sort_key"]) if (page and has_next) else None

        for c in page:
            c.pop("_sort_key", None)
        return {"items": page, "next_cursor": next_cursor, "has_next": has_next}
```

Note: `settings.OCR.CONFIDENCE_MIN` / `LANG_SCORE_MIN` — `settings` is already imported (`from config.settings import settings`). Verify the exact attribute path with `python -c "from config.settings import settings; print(settings.OCR.CONFIDENCE_MIN, settings.OCR.LANG_SCORE_MIN)"` (should print `0.4 0.3`). `build_bow.py:94-95` uses exactly this path.

- [ ] **Step 4: Run to verify pass**

Run: `cd Backend && pytest tests/test_ingestion_service.py -v`
Expected: PASS — new class plus all pre-existing `resolve` tests still green.

- [ ] **Step 5: Commit**

```bash
git add Backend/app/services/ingestion_service.py Backend/tests/test_ingestion_service.py
git commit -m "feat: cursor-paginate ingestion list_clusters, sort by tightest edge

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 3: Backend — `ClusterPage` schema, model, route; regenerate types; docs

**Files:**
- Create: `shared/schemas/ingestionclusterpage.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Modify: `Backend/app/api/ingestion.py` (`ClusterPage` model; `list_clusters` route ~line 99)
- Modify: `Backend/tests/test_ingestion_endpoints.py` (`TestListClusters`)
- Modify: `backend_api.md` ("List Clusters", ~line 938)
- Regenerate: `Frontend/memes-frontend/src/types/generated/all.d.ts`, Android DTOs

**Interfaces:**
- Consumes: `IngestionService.list_clusters(tier, cursor=, limit=)` from Task 2.
- Produces: `GET /api/ingestion/clusters/{tier}?cursor=&limit=` → JSON `{items: Cluster[], next_cursor: string|null, has_next: boolean}`. TS type `IngestionClusterPage` in `types/generated/all.d.ts`.

- [ ] **Step 1: Write the failing endpoint tests**

Replace `TestListClusters` in `Backend/tests/test_ingestion_endpoints.py`:

```python
class TestListClusters:
    def test_returns_cluster_page_for_tier(self, client, mock_service):
        mock_service.list_clusters.return_value = {
            "items": [{
                "members": [
                    {"image_id": "11111111-1111-1111-1111-111111111111", "filename": "a.jpg",
                     "status": "pending", "ocr_text": "Не смешно"},
                ],
                "edges": [],
            }],
            "next_cursor": "0.05|11111111-1111-1111-1111-111111111111",
            "has_next": True,
        }
        response = client.get("/api/ingestion/clusters/tier_a")
        assert response.status_code == 200
        body = response.json()
        assert body["has_next"] is True
        assert body["next_cursor"] == "0.05|11111111-1111-1111-1111-111111111111"
        assert body["items"][0]["members"][0]["ocr_text"] == "Не смешно"
        mock_service.list_clusters.assert_awaited_once_with("tier_a", cursor=None, limit=40)

    def test_forwards_cursor_and_limit(self, client, mock_service):
        mock_service.list_clusters.return_value = {"items": [], "next_cursor": None, "has_next": False}
        response = client.get("/api/ingestion/clusters/tier_b?cursor=0.1%7Cabc&limit=10")
        assert response.status_code == 200
        mock_service.list_clusters.assert_awaited_once_with("tier_b", cursor="0.1|abc", limit=10)

    def test_rejects_out_of_range_limit(self, client, mock_service):
        assert client.get("/api/ingestion/clusters/tier_a?limit=0").status_code == 422
        assert client.get("/api/ingestion/clusters/tier_a?limit=999").status_code == 422

    def test_rejects_unknown_tier(self, client, mock_service):
        assert client.get("/api/ingestion/clusters/tier_z").status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py::TestListClusters -v`
Expected: FAIL — route returns a list, no `cursor`/`limit` params, `assert_awaited_once_with` mismatch.

- [ ] **Step 3: Create the schema file**

`shared/schemas/ingestionclusterpage.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "ingestionclusterpage.schema.json",
  "title": "IngestionClusterPage",
  "type": "object",
  "properties": {
    "items": { "type": "array", "items": { "$ref": "./ingestioncluster.schema.json" } },
    "next_cursor": { "type": ["string", "null"] },
    "has_next": { "type": "boolean" }
  },
  "required": ["items", "next_cursor", "has_next"]
}
```

- [ ] **Step 4: Register it in `all.schema.json`**

In `shared/schemas/all.schema.json`, add after the `IngestionCluster` line (~line 31):

```json
    "IngestionClusterPage":       { "$ref": "ingestionclusterpage.schema.json" },
```

(Match the surrounding key alignment/comma style exactly.)

- [ ] **Step 5: Add the Pydantic model and update the route**

In `Backend/app/api/ingestion.py`, after `class Cluster(BaseModel):` (~line 49):

```python
class ClusterPage(BaseModel):
    items: list[Cluster]
    next_cursor: Optional[str]
    has_next: bool
```

Change the import line `from fastapi import APIRouter, Depends` → `from fastapi import APIRouter, Depends, Query`.

Replace the `list_clusters` route (~line 99):

```python
@router.get("/clusters/{tier}", response_model=ClusterPage)
async def list_clusters(
    tier: Tier,
    cursor: Optional[str] = None,
    limit: int = Query(40, ge=1, le=200),
    service: IngestionService = Depends(get_ingestion_service),
):
    return await service.list_clusters(tier, cursor=cursor, limit=limit)
```

- [ ] **Step 6: Run to verify pass**

Run: `cd Backend && pytest tests/test_ingestion_endpoints.py -v`
Expected: PASS.

- [ ] **Step 7: Regenerate TS + Kotlin types**

```bash
cd Frontend && bash generate-types.sh && cd ..
python AndroidClient/scripts/generate_dtos.py
git status --porcelain   # expect all.d.ts + some AndroidClient/*.kt changes
```

Verify `IngestionClusterPage` now appears in `Frontend/memes-frontend/src/types/generated/all.d.ts` with `items`, `next_cursor`, `has_next`. If `generate-types.sh` needs `npx`/network and fails in the sandbox, hand-add the interface to `all.d.ts` in the exact style of the neighboring generated interfaces and note in the commit that the generator must be re-run in CI.

- [ ] **Step 8: Update `backend_api.md`**

In the "List Clusters" section (~line 938), change:
- Add **Query params**: `cursor` (optional, opaque — omit for the first page), `limit` (default `40`, 1–200).
- **Response**: `ClusterPage` (was `Cluster[]`).
- Replace the example response with:

```json
{
  "items": [
    {
      "members": [
        { "image_id": "1a2b...", "filename": "meme_01.jpg", "status": "pending", "ocr_text": "Не смешно" }
      ],
      "edges": [
        { "image_id1": "1a2b...", "image_id2": "3c4d...", "distance": 0.041, "match_source": "clip" }
      ]
    }
  ],
  "next_cursor": "0.041|1a2b...",
  "has_next": true
}
```

Add one line: *"Clusters are ordered by their tightest edge (closest match first). `next_cursor` is `null` on the last page; pass it back verbatim as `?cursor=` for the next page. A stale/invalid cursor restarts from the first page rather than erroring."*

- [ ] **Step 9: Commit**

```bash
git add shared/schemas/ Backend/app/api/ingestion.py Backend/tests/test_ingestion_endpoints.py backend_api.md Frontend/memes-frontend/src/types/generated/all.d.ts AndroidClient/
git commit -m "feat: ClusterPage response for paginated ingestion clusters endpoint

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

- [ ] **Step 10: Backend regression sweep**

Run: `cd Backend && pytest -q`
Expected: PASS (whole Backend suite). Also boot-check: `python -c "import Backend.app.main"` → no import errors.

---

## Task 4: Frontend — API client + mock

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts` (~line 73)
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts` (~line 364)
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts` (~line 36)
- Check: `Frontend/memes-frontend/src/api/http/HttpMemesApi.test.ts` (if it covers `getIngestionClusters`)

**Interfaces:**
- Consumes: `IngestionClusterPage` type from Task 3.
- Produces: `MemesApi.getIngestionClusters(tier: IngestionTier, cursor?: string) => Promise<IngestionClusterPage>`.

- [ ] **Step 1: Update the interface**

`MemesApi.ts`: add `IngestionClusterPage` to the type import from `../types/generated/all`, and change:

```ts
  getIngestionClusters(tier: IngestionTier, cursor?: string): Promise<IngestionClusterPage>;
```

- [ ] **Step 2: Update the HTTP impl**

`HttpMemesApi.ts`:

```ts
  async getIngestionClusters(tier: IngestionTier, cursor?: string): Promise<IngestionClusterPage> {
    const params = new URLSearchParams({ limit: "40" })
    if (cursor) params.set("cursor", cursor)
    const res = await fetch(`${this.baseUrl}/api/ingestion/clusters/${tier}?${params}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch ${tier} clusters: ${res.status}`)
    return res.json()
  }
```

Add `IngestionClusterPage` to its type import too.

- [ ] **Step 3: Update the mock**

`test/mockApi.ts`:

```ts
    getIngestionClusters: vi.fn().mockResolvedValue({ items: [], next_cursor: null, has_next: false }),
```

- [ ] **Step 4: Type-check + existing tests**

Run: `cd Frontend/memes-frontend && npx tsc -b`
Expected: FAIL only in `IngestionReviewPage.tsx` / `IngestionReviewPage.test.tsx` (they still expect an array) — those are Task 5+. No errors in `HttpMemesApi.ts` / `MemesApi.ts` / `mockApi.ts`.

Run: `npx vitest run src/api`
Expected: PASS (or update `HttpMemesApi.test.ts`'s `getIngestionClusters` case to assert the `?limit=40` URL and page return shape, if such a test exists).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/ Frontend/memes-frontend/src/test/mockApi.ts
git commit -m "feat: getIngestionClusters returns a paginated ClusterPage

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 5: Frontend — MemberTile + ClusterRow (hybrid layout, whole image, full OCR)

**Files:**
- Create: `Frontend/memes-frontend/src/components/ingestion/types.ts`
- Create: `Frontend/memes-frontend/src/components/ingestion/MemberTile.tsx`
- Create: `Frontend/memes-frontend/src/components/ingestion/ClusterRow.tsx`
- Create: `Frontend/memes-frontend/src/components/ingestion/ClusterRow.test.tsx`

**Interfaces:**
- Consumes: `IngestionCluster`, `IngestionClusterMember` types; `MemesApi`.
- Produces:
  - `types.ts`: `export type Decision = "reject" | "keep"`
  - `MemberTile` props: `{ memesApi: MemesApi; member: IngestionClusterMember; edgeLabels: string[]; decision: Decision | undefined; onDecide: (d: Decision) => void; onHoverPreview: () => void; onLeavePreview: () => void; onPeek: () => void }`
  - `ClusterRow` props: `{ memesApi: MemesApi; cluster: IngestionCluster; decisions: Record<string, Decision | undefined>; onDecide: (imageId: string, d: Decision) => void; onSubmit: () => void; submitting: boolean; onHoverPreview: (member: IngestionClusterMember) => void; onLeavePreview: () => void; onPeek: (member: IngestionClusterMember) => void }`

- [ ] **Step 1: Write the failing test**

`ClusterRow.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ClusterRow } from './ClusterRow'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionCluster } from '../../types/generated/all'

const cluster: IngestionCluster = {
  members: [
    { image_id: 'a', filename: 'a.jpg', status: 'pending', ocr_text: 'Не смешно, совсем не смешно, длинный текст который раньше обрезался' },
    { image_id: 'b', filename: 'b.jpg', status: 'active', ocr_text: 'Не смешно' },
  ],
  edges: [{ image_id1: 'a', image_id2: 'b', distance: 0.04, match_source: 'clip' }],
}

describe('ClusterRow', () => {
  it('renders every member image uncropped (object-contain) with full OCR text, not clamped', () => {
    render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    const imgs = screen.getAllByRole('img')
    expect(imgs).toHaveLength(2)
    imgs.forEach(img => {
      expect(img.className).toContain('object-contain')
      expect(img.className).not.toContain('object-cover')
      expect(img).toHaveAttribute('loading', 'lazy')
      expect(img).toHaveAttribute('decoding', 'async')
    })
    expect(screen.getByText(/длинный текст который раньше обрезался/)).toBeInTheDocument()
    expect(document.querySelector('.line-clamp-3')).toBeNull()
  })

  it('shows Keep/Reject only for pending members and calls onDecide', async () => {
    const onDecide = vi.fn()
    render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={onDecide} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getAllByRole('button', { name: /keep/i })).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onDecide).toHaveBeenCalledWith('a', 'reject')
  })

  it('fires onHoverPreview with the member on pointer enter', async () => {
    const onHoverPreview = vi.fn()
    render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={onHoverPreview} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    await userEvent.hover(screen.getAllByRole('img')[0])
    expect(onHoverPreview).toHaveBeenCalledWith(cluster.members[0])
  })

  it('disables the per-cluster submit until a pending member has a decision', () => {
    const { rerender } = render(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{}}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeDisabled()
    rerender(<ClusterRow memesApi={makeMockApi()} cluster={cluster} decisions={{ a: 'keep' }}
      onDecide={vi.fn()} onSubmit={vi.fn()} submitting={false}
      onHoverPreview={vi.fn()} onLeavePreview={vi.fn()} onPeek={vi.fn()} />)
    expect(screen.getByRole('button', { name: /submit decisions/i })).toBeEnabled()
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Frontend/memes-frontend && npx vitest run src/components/ingestion/ClusterRow.test.tsx`
Expected: FAIL — modules don't exist.

- [ ] **Step 3: Implement `types.ts`**

```ts
export type Decision = "reject" | "keep"
```

- [ ] **Step 4: Implement `MemberTile.tsx`**

```tsx
import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"

type Props = {
  memesApi: MemesApi
  member: IngestionClusterMember
  edgeLabels: string[]
  decision: Decision | undefined
  onDecide: (d: Decision) => void
  onHoverPreview: () => void
  onLeavePreview: () => void
  onPeek: () => void
}

const BTN = "flex-1 text-xs rounded px-2 py-1 transition-colors active:scale-[.97] active:brightness-95"

export function MemberTile({
  memesApi, member, edgeLabels, decision, onDecide, onHoverPreview, onLeavePreview, onPeek,
}: Props) {
  const isPending = member.status === "pending"
  return (
    <div
      className={`shrink-0 w-[22rem] max-w-[80vw] border rounded-lg p-2 ${decision === "reject" ? "opacity-40" : ""}`}
      tabIndex={0}
      onMouseEnter={onHoverPreview}
      onMouseLeave={onLeavePreview}
      onFocus={onHoverPreview}
      onBlur={onLeavePreview}
      onKeyDown={(e) => { if (e.key === "Enter") onPeek() }}
    >
      <img
        src={memesApi.getImageUrlById(member.image_id)}
        alt={member.filename}
        loading="lazy"
        decoding="async"
        className="w-full max-h-[55vh] object-contain rounded bg-gray-50 cursor-zoom-in"
        onClick={onPeek}
      />
      <div className="text-xs mt-1 truncate" title={member.filename}>{member.filename}</div>
      <div className="text-xs">
        <span className={isPending ? "text-blue-600" : "text-gray-400"}>{member.status}</span>
      </div>
      {member.ocr_text && (
        <div className="text-[13px] text-gray-700 mt-1 max-h-40 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-gray-200 pl-2">
          {member.ocr_text}
        </div>
      )}
      {edgeLabels.map((label) => (
        <div key={label} className="text-[11px] text-gray-500 mt-0.5">{label}</div>
      ))}
      {isPending && (
        <div className="flex gap-1 mt-2">
          <button
            className={`${BTN} ${decision === "keep" ? "bg-green-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("keep")}
          >Keep</button>
          <button
            className={`${BTN} ${decision === "reject" ? "bg-red-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("reject")}
          >Reject</button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Implement `ClusterRow.tsx`**

```tsx
import type { MemesApi } from "../../api/MemesApi"
import type { IngestionCluster, IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"
import { MemberTile } from "./MemberTile"

type Props = {
  memesApi: MemesApi
  cluster: IngestionCluster
  decisions: Record<string, Decision | undefined>
  onDecide: (imageId: string, d: Decision) => void
  onSubmit: () => void
  submitting: boolean
  onHoverPreview: (member: IngestionClusterMember) => void
  onLeavePreview: () => void
  onPeek: (member: IngestionClusterMember) => void
}

export function ClusterRow({
  memesApi, cluster, decisions, onDecide, onSubmit, submitting,
  onHoverPreview, onLeavePreview, onPeek,
}: Props) {
  const hasPendingDecision = cluster.members.some(
    (m) => m.status === "pending" && decisions[m.image_id] !== undefined
  )
  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-4">
      <div className="flex gap-3 overflow-x-auto pb-2">
        {cluster.members.map((member) => {
          const edgeLabels = cluster.edges
            .filter((e) => e.image_id1 === member.image_id || e.image_id2 === member.image_id)
            .map((e) => `${e.distance.toFixed(3)} (${e.match_source ?? "?"})`)
          return (
            <MemberTile
              key={member.image_id}
              memesApi={memesApi}
              member={member}
              edgeLabels={edgeLabels}
              decision={decisions[member.image_id]}
              onDecide={(d) => onDecide(member.image_id, d)}
              onHoverPreview={() => onHoverPreview(member)}
              onLeavePreview={onLeavePreview}
              onPeek={() => onPeek(member)}
            />
          )
        })}
      </div>
      <button
        className="mt-3 text-sm rounded bg-blue-600 text-white px-3 py-1 transition-colors active:scale-[.97] disabled:opacity-40"
        disabled={!hasPendingDecision || submitting}
        onClick={onSubmit}
      >
        {submitting ? "Submitting…" : "Submit decisions"}
      </button>
    </div>
  )
}
```

- [ ] **Step 6: Run to verify pass**

Run: `cd Frontend/memes-frontend && npx vitest run src/components/ingestion/ClusterRow.test.tsx`
Expected: PASS (4).

- [ ] **Step 7: Commit**

```bash
git add Frontend/memes-frontend/src/components/ingestion/
git commit -m "feat: ClusterRow/MemberTile with uncropped images and full OCR text

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 6: Frontend — DockedPreview pane

**Files:**
- Create: `Frontend/memes-frontend/src/components/ingestion/DockedPreview.tsx`
- Create: `Frontend/memes-frontend/src/components/ingestion/DockedPreview.test.tsx`

**Interfaces:**
- Consumes: `MemesApi`, `IngestionClusterMember`, `Decision`.
- Produces: `DockedPreview` props `{ memesApi: MemesApi; member: IngestionClusterMember | null; decision: Decision | undefined; onDecide: (d: Decision) => void; onMouseEnter: () => void; onMouseLeave: () => void }`. Renders nothing when `member` is null.

- [ ] **Step 1: Write the failing test**

`DockedPreview.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { DockedPreview } from './DockedPreview'
import { makeMockApi } from '../../test/mockApi'
import type { IngestionClusterMember } from '../../types/generated/all'

const member: IngestionClusterMember = {
  image_id: 'a', filename: 'a.jpg', status: 'pending', ocr_text: 'полный текст OCR',
}

describe('DockedPreview', () => {
  it('renders nothing when member is null', () => {
    const { container } = render(<DockedPreview memesApi={makeMockApi()} member={null}
      decision={undefined} onDecide={vi.fn()} onMouseEnter={vi.fn()} onMouseLeave={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the same image URL as the tile (cache hit) plus full OCR and Keep/Reject', async () => {
    const api = makeMockApi()
    const onDecide = vi.fn()
    render(<DockedPreview memesApi={api} member={member}
      decision={undefined} onDecide={onDecide} onMouseEnter={vi.fn()} onMouseLeave={vi.fn()} />)
    expect(screen.getByRole('img')).toHaveAttribute('src', api.getImageUrlById('a'))
    expect(screen.getByText('полный текст OCR')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onDecide).toHaveBeenCalledWith('reject')
  })

  it('does not render Keep/Reject for a non-pending member', () => {
    render(<DockedPreview memesApi={makeMockApi()} member={{ ...member, status: 'active' }}
      decision={undefined} onDecide={vi.fn()} onMouseEnter={vi.fn()} onMouseLeave={vi.fn()} />)
    expect(screen.queryByRole('button', { name: /reject/i })).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `cd Frontend/memes-frontend && npx vitest run src/components/ingestion/DockedPreview.test.tsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `DockedPreview.tsx`**

```tsx
import type { MemesApi } from "../../api/MemesApi"
import type { IngestionClusterMember } from "../../types/generated/all"
import type { Decision } from "./types"

type Props = {
  memesApi: MemesApi
  member: IngestionClusterMember | null
  decision: Decision | undefined
  onDecide: (d: Decision) => void
  onMouseEnter: () => void
  onMouseLeave: () => void
}

export function DockedPreview({ memesApi, member, decision, onDecide, onMouseEnter, onMouseLeave }: Props) {
  if (!member) return null
  return (
    <aside
      className="hidden lg:flex flex-col fixed top-0 right-0 h-screen w-[40vw] max-w-[720px] z-40 bg-white/98 border-l shadow-2xl p-4 gap-3"
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div className="text-xs text-gray-500 truncate" title={member.filename}>{member.filename}</div>
      <div className="flex-1 overflow-auto flex items-start justify-center">
        <img
          src={memesApi.getImageUrlById(member.image_id)}
          alt={member.filename}
          decoding="async"
          className="max-w-full"
        />
      </div>
      {member.ocr_text && (
        <div className="text-sm text-gray-800 max-h-48 overflow-y-auto whitespace-pre-wrap break-words shrink-0">
          {member.ocr_text}
        </div>
      )}
      {member.status === "pending" && (
        <div className="flex gap-2 shrink-0">
          <button
            className={`flex-1 rounded px-3 py-2 text-sm transition-colors active:scale-[.97] ${decision === "keep" ? "bg-green-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("keep")}
          >Keep</button>
          <button
            className={`flex-1 rounded px-3 py-2 text-sm transition-colors active:scale-[.97] ${decision === "reject" ? "bg-red-600 text-white" : "bg-gray-100 hover:bg-gray-200"}`}
            onClick={() => onDecide("reject")}
          >Reject</button>
        </div>
      )}
    </aside>
  )
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd Frontend/memes-frontend && npx vitest run src/components/ingestion/DockedPreview.test.tsx`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/components/ingestion/DockedPreview.tsx Frontend/memes-frontend/src/components/ingestion/DockedPreview.test.tsx
git commit -m "feat: docked full-size preview pane for ingestion review

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 7: Frontend — rewrite `IngestionReviewPage` (virtualized list, pagination, docked pane wiring, action bar, optimistic removal)

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx` (near-total rewrite of the render + submit paths; keep `tierForStage`, `formatResolveSummary`, `TIER_LABEL`, `CONFIRM_ALL_TIMEOUT_MS`, `StatusBanner`)
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx` (rewrite)

**Interfaces:**
- Consumes: `getIngestionClusters(tier, cursor?) => Promise<IngestionClusterPage>` (Task 4); `ClusterRow` (Task 5); `DockedPreview` (Task 6); `Decision` (Task 5).
- Produces: the finished page. No exports other than the default component.

- [ ] **Step 1: Rewrite the test file**

Replace `IngestionReviewPage.test.tsx` with coverage for the new behaviour. Keep the still-valid cases (no-run message, tier-change clears decisions, OCR-prepass waiting message, submit-failure inline). Add/replace:

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import IngestionReviewPage from './IngestionReviewPage'
import { makeMockApi } from '../test/mockApi'
import type { IngestionClusterPage, IngestionCluster } from '../types/generated/all'

const runStatus = { run_id: 'r1', status: 'started', stage: 'tier_a_review', stats: {}, created_at: '', completed_at: null }

function cl(id: string, dist = 0.05): IngestionCluster {
  return {
    members: [
      { image_id: `${id}-1`, filename: `${id}-1.jpg`, status: 'pending', ocr_text: 'текст' },
      { image_id: `${id}-2`, filename: `${id}-2.jpg`, status: 'active', ocr_text: 'текст' },
    ],
    edges: [{ image_id1: `${id}-1`, image_id2: `${id}-2`, distance: dist, match_source: 'clip' }],
  }
}
const page = (items: IngestionCluster[], next: string | null = null): IngestionClusterPage =>
  ({ items, next_cursor: next, has_next: next !== null })

describe('IngestionReviewPage', () => {
  it('loads the first page and renders clusters', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters: vi.fn().mockResolvedValue(page([cl('a'), cl('b')])),
    })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText('a-1.jpg')).toBeInTheDocument()
    expect(screen.getByText('b-1.jpg')).toBeInTheDocument()
    expect(api.getIngestionClusters).toHaveBeenCalledWith('tier_a', undefined)
  })

  it('fetches the next page when the list end is reached', async () => {
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')], 'CURSOR1'))
      .mockResolvedValueOnce(page([cl('b')]))
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    // Virtuoso in jsdom: trigger via the exposed endReached test hook (see impl note) or
    // scroll the container. Simplest: assert the "Load more" fallback button the impl renders
    // when has_next is true (jsdom has no real scrolling).
    await userEvent.click(screen.getByRole('button', { name: /load more/i }))
    await waitFor(() => expect(getIngestionClusters).toHaveBeenCalledWith('tier_a', 'CURSOR1'))
    expect(await screen.findByText('b-1.jpg')).toBeInTheDocument()
  })

  it('optimistically removes a fully-resolved cluster on submit and does not refetch page 1', async () => {
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a'), cl('b')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /reject/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /^submit decisions$/i })[0])
    await waitFor(() => expect(screen.queryByText('a-1.jpg')).toBeNull())
    expect(screen.getByText('b-1.jpg')).toBeInTheDocument()
    expect(getIngestionClusters).toHaveBeenCalledTimes(1) // no reload on the happy path
  })

  it('re-inserts the cluster and shows a message when the server reports a failed decision', async () => {
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({
      rejected: [], kept: [], failed: [{ image_id: 'a-1', decision: 'reject', error: 'boom' }], move_failed: [],
    })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /reject/i }))
    await userEvent.click(screen.getByRole('button', { name: /^submit decisions$/i }))
    expect(await screen.findByText(/failed to apply/i)).toBeInTheDocument()
    expect(screen.getByText('a-1.jpg')).toBeInTheDocument() // back in the list
  })

  it('keeps the "Submit all" bar visible and submits every decision', async () => {
    const getIngestionClusters = vi.fn().mockResolvedValue(page([cl('a'), cl('b')]))
    const resolveIngestionCluster = vi.fn().mockResolvedValue({ rejected: ['a-1', 'b-1'], kept: [], failed: [], move_failed: [] })
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(runStatus),
      getIngestionClusters, resolveIngestionCluster,
    })
    render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getAllByRole('button', { name: /reject/i })[0])
    await userEvent.click(screen.getAllByRole('button', { name: /reject/i })[1])
    const bar = screen.getByRole('button', { name: /submit all decisions/i })
    await userEvent.click(bar)                                   // arm confirm
    await userEvent.click(screen.getByRole('button', { name: /confirm/i }))
    await waitFor(() => expect(resolveIngestionCluster).toHaveBeenCalledWith('tier_a',
      expect.arrayContaining([
        { image_id: 'a-1', decision: 'reject' }, { image_id: 'b-1', decision: 'reject' },
      ])))
    await waitFor(() => expect(screen.queryByText('a-1.jpg')).toBeNull())
  })

  it('shows the no-run message when there is no active ingestion run', async () => {
    const api = makeMockApi({ getIngestionRunStatus: vi.fn().mockResolvedValue(null) })
    render(<IngestionReviewPage memesApi={api} />)
    expect(await screen.findByText(/no ingestion run/i)).toBeInTheDocument()
  })

  it('clears local decisions when the review tier changes', async () => {
    const getIngestionRunStatus = vi.fn()
      .mockResolvedValueOnce({ ...runStatus, stage: 'tier_a_review' })
      .mockResolvedValueOnce({ ...runStatus, stage: 'tier_b_review' })
    const getIngestionClusters = vi.fn()
      .mockResolvedValueOnce(page([cl('a')]))
      .mockResolvedValueOnce(page([cl('a')]))
    const api = makeMockApi({ getIngestionRunStatus, getIngestionClusters })
    const { rerender } = render(<IngestionReviewPage memesApi={api} />)
    await screen.findByText('a-1.jpg')
    await userEvent.click(screen.getByRole('button', { name: /keep/i }))
    // trigger a reload (impl: a Refresh button, or re-mount). Then the tier-change effect clears.
    rerender(<IngestionReviewPage memesApi={api} key="2" />)
    await waitFor(() => {
      const keep = screen.getAllByRole('button', { name: /keep/i })[0]
      expect(keep.className).not.toContain('bg-green-600')
    })
  })
})
```

Note the two test seams the implementation must provide for jsdom (no real scroll):
1. A visible **"Load more"** button rendered whenever `hasNext && !loadingMore` (also a real UX affordance — keep it).
2. Nothing else Virtuoso-specific is asserted; render `<Virtuoso>` normally, it degrades to rendering items in jsdom.

- [ ] **Step 2: Run to verify failure**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: FAIL across the board.

- [ ] **Step 3: Rewrite `IngestionReviewPage.tsx`**

Keep the top-of-file helpers (`tierForStage`, `formatResolveSummary`, `TIER_LABEL`, `CONFIRM_ALL_TIMEOUT_MS`, `StatusBanner`). Replace `MemberTile` (now imported) and the component body. Key structure:

```tsx
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import type { MemesApi } from "../api/MemesApi"
import type { IngestionCluster, IngestionClusterMember, IngestionResolveResponse, IngestionRunStatus } from "../types/generated/all"
import { Modal } from "../components/Modal"
import { ClusterRow } from "../components/ingestion/ClusterRow"
import { DockedPreview } from "../components/ingestion/DockedPreview"
import type { Decision } from "../components/ingestion/types"

// ... keep tierForStage / formatResolveSummary / TIER_LABEL / CONFIRM_ALL_TIMEOUT_MS / StatusBanner ...

const PAGE_LIMIT = 40

export default function IngestionReviewPage({ memesApi }: { memesApi: MemesApi }) {
  const [status, setStatus] = useState<IngestionRunStatus | null>(null)
  const [clusters, setClusters] = useState<IngestionCluster[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasNext, setHasNext] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [decisions, setDecisions] = useState<Record<string, Decision | undefined>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState<number | "all" | null>(null)
  const [preview, setPreview] = useState<IngestionClusterMember | null>(null)
  const [peek, setPeek] = useState<IngestionClusterMember | null>(null)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const confirmAllTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previewCloseRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const preloadedRef = useRef<Set<string>>(new Set())

  const tier = status ? tierForStage(status.stage) : null

  const load = useCallback(() => {
    setLoading(true)
    return memesApi.getIngestionRunStatus()
      .then((s) => {
        setStatus(s)
        setError(null)
        const t = s ? tierForStage(s.stage) : null
        return t ? memesApi.getIngestionClusters(t, undefined) : { items: [], next_cursor: null, has_next: false }
      })
      .then((pageResult) => {
        setClusters(pageResult.items)
        setNextCursor(pageResult.next_cursor)
        setHasNext(pageResult.has_next)
      })
      .catch((e: unknown) => {
        setStatus(null); setClusters([]); setNextCursor(null); setHasNext(false)
        setError(e instanceof Error ? e.message : "Failed to load ingestion review")
      })
      .finally(() => setLoading(false))
  }, [memesApi])

  const loadedRef = useRef(false)
  useEffect(() => { if (loadedRef.current) return; loadedRef.current = true; load() }, [load])

  useEffect(() => () => {
    if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
    if (previewCloseRef.current) clearTimeout(previewCloseRef.current)
  }, [])

  // tier-change: wipe local decisions (unchanged rationale from the old effect)
  useEffect(() => {
    if (!tier) return
    void (async () => { await Promise.resolve(); setDecisions({}) })()
  }, [tier])

  // prune decisions whose target is no longer a pending member of any loaded cluster
  useEffect(() => {
    void (async () => {
      await Promise.resolve()
      const pendingIds = new Set(
        clusters.flatMap((c) => c.members.filter((m) => m.status === "pending").map((m) => m.image_id))
      )
      setDecisions((prev) => {
        const next: Record<string, Decision | undefined> = {}
        for (const [id, d] of Object.entries(prev)) if (pendingIds.has(id)) next[id] = d
        return next
      })
    })()
  }, [clusters])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasNext || !nextCursor || !tier) return
    setLoadingMore(true)
    try {
      const pageResult = await memesApi.getIngestionClusters(tier, nextCursor)
      setClusters((prev) => [...prev, ...pageResult.items])
      setNextCursor(pageResult.next_cursor)
      setHasNext(pageResult.has_next)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load more clusters")
    } finally {
      setLoadingMore(false)
    }
  }, [memesApi, tier, nextCursor, hasNext, loadingMore])

  function setDecision(memberId: string, decision: Decision) {
    setDecisions((prev) => ({ ...prev, [memberId]: prev[memberId] === decision ? undefined : decision }))
  }

  function openPreview(member: IngestionClusterMember) {
    if (previewCloseRef.current) clearTimeout(previewCloseRef.current)
    setPreview(member)
    const url = memesApi.getImageUrlById(member.image_id)
    if (!preloadedRef.current.has(url)) {
      preloadedRef.current.add(url)
      const img = new Image()
      img.src = url
    }
  }
  function closePreviewSoon() {
    if (previewCloseRef.current) clearTimeout(previewCloseRef.current)
    previewCloseRef.current = setTimeout(() => setPreview(null), 120)
  }

  // ---- submit paths (optimistic) ----

  function decidedPendingIn(cluster: IngestionCluster): { image_id: string; decision: Decision }[] {
    const out: { image_id: string; decision: Decision }[] = []
    for (const m of cluster.members) {
      if (m.status !== "pending") continue
      const d = decisions[m.image_id]
      if (d !== undefined) out.push({ image_id: m.image_id, decision: d })
    }
    return out
  }
  function isFullyResolved(cluster: IngestionCluster): boolean {
    const pending = cluster.members.filter((m) => m.status === "pending")
    return pending.length > 0 && pending.every((m) => decisions[m.image_id] !== undefined)
  }

  async function runSubmit(which: number | "all", toSubmit: { cluster: IngestionCluster; index: number }[]) {
    if (!tier) return
    const payload = toSubmit.flatMap(({ cluster }) => decidedPendingIn(cluster))
    if (payload.length === 0) return
    // snapshot for rollback: [index, cluster] pairs of clusters we optimistically remove
    const removed = toSubmit.filter(({ cluster }) => isFullyResolved(cluster))
    const removedIds = new Set(removed.map(({ cluster }) => cluster))
    setSubmitting(which)
    setClusters((prev) => prev.filter((c) => !removedIds.has(c)))
    setDecisions((prev) => {
      const next = { ...prev }
      for (const { image_id } of payload) delete next[image_id]
      return next
    })
    try {
      const response: IngestionResolveResponse = await memesApi.resolveIngestionCluster(tier, payload)
      const failedIds = new Set([...response.failed.map((f) => f.image_id)])
      if (failedIds.size > 0) {
        // re-insert clusters that still have a failed member, at their original position
        setClusters((prev) => {
          const reinsert = removed
            .filter(({ cluster }) => cluster.members.some((m) => failedIds.has(m.image_id)))
            .sort((a, b) => a.index - b.index)
          const copy = [...prev]
          for (const { cluster, index } of reinsert) copy.splice(Math.min(index, copy.length), 0, cluster)
          return copy
        })
      }
      const summary = formatResolveSummary(response)
      if (summary) setError(summary)
      else setError(null)
    } catch (e: unknown) {
      // full rollback
      setClusters((prev) => {
        const copy = [...prev]
        for (const { cluster, index } of [...removed].sort((a, b) => a.index - b.index)) {
          copy.splice(Math.min(index, copy.length), 0, cluster)
        }
        return copy
      })
      setError(e instanceof Error ? e.message : "Failed to submit decisions")
    } finally {
      setSubmitting(null)
    }
  }

  const submitCluster = (index: number) => runSubmit(index, [{ cluster: clusters[index], index }])
  const submitAll = () => runSubmit("all", clusters.map((cluster, index) => ({ cluster, index })))

  function handleSubmitAllClick() {
    if (confirmingAll) {
      if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
      setConfirmingAll(false)
      submitAll()
      return
    }
    setConfirmingAll(true)
    confirmAllTimeoutRef.current = setTimeout(() => setConfirmingAll(false), CONFIRM_ALL_TIMEOUT_MS)
  }

  const { clustersWithPendingCount, allPendingCount } = useMemo(() => {
    let c = 0, i = 0
    for (const cluster of clusters) {
      const n = decidedPendingIn(cluster).length
      if (n > 0) { c++; i += n }
    }
    return { clustersWithPendingCount: c, allPendingCount: i }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusters, decisions])

  // ---- render ----
  if (loading) return <Shell><p className="text-sm text-gray-400">Loading…</p></Shell>
  if (error && !status) return <Shell><p className="text-sm text-red-500 mb-3">{error}</p>
    <button className="text-sm rounded bg-blue-600 text-white px-3 py-1" onClick={() => load()}>Retry</button></Shell>
  if (!status) return <Shell><p className="text-sm text-gray-400">No ingestion run is currently in progress.</p></Shell>

  const previewDecision = preview ? decisions[preview.image_id] : undefined

  return (
    <div className={preview ? "lg:pr-[42vw]" : ""}>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review{tier ? ` — ${TIER_LABEL[tier]}` : ""}</h1>
      <StatusBanner status={status} />
      {error && <p className="text-sm text-red-500 mb-3">{error}</p>}

      {!tier && (
        <p className="text-sm text-gray-400">
          {status.stage === "ocr_prepass"
            ? "OCR is running — Tier A review will be available once it finishes."
            : "Candidates haven't been computed for this run yet."}
        </p>
      )}
      {tier && clusters.length === 0 && !hasNext && (
        <p className="text-sm text-gray-400">No {TIER_LABEL[tier]} clusters need review right now.</p>
      )}

      {tier && clusters.length > 0 && (
        <Virtuoso
          useWindowScroll
          data={clusters}
          endReached={() => { void loadMore() }}
          increaseViewportBy={{ top: 400, bottom: 1200 }}
          itemContent={(index, cluster) => (
            <ClusterRow
              memesApi={memesApi}
              cluster={cluster}
              decisions={decisions}
              onDecide={setDecision}
              onSubmit={() => submitCluster(index)}
              submitting={submitting === index || submitting === "all"}
              onHoverPreview={openPreview}
              onLeavePreview={closePreviewSoon}
              onPeek={setPeek}
            />
          )}
        />
      )}

      {tier && hasNext && (
        <div className="py-4 text-center">
          <button
            className="text-sm rounded bg-gray-100 hover:bg-gray-200 px-4 py-2 disabled:opacity-40"
            disabled={loadingMore}
            onClick={() => void loadMore()}
          >
            {loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      )}

      {clustersWithPendingCount > 0 && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-3 rounded-full bg-white shadow-2xl border px-5 py-2">
          <span className="text-xs text-gray-500">{status.stage}</span>
          <button
            className={`text-sm rounded-full px-4 py-1.5 text-white transition-colors active:scale-[.97] disabled:opacity-40 ${confirmingAll ? "bg-amber-500" : "bg-blue-600"}`}
            disabled={submitting !== null}
            onClick={handleSubmitAllClick}
          >
            {submitting === "all"
              ? "Submitting…"
              : confirmingAll
                ? `Confirm? (${clustersWithPendingCount} cluster${clustersWithPendingCount === 1 ? "" : "s"}, ${allPendingCount} image${allPendingCount === 1 ? "" : "s"})`
                : `Submit all decisions (${clustersWithPendingCount} cluster${clustersWithPendingCount === 1 ? "" : "s"}, ${allPendingCount} image${allPendingCount === 1 ? "" : "s"})`}
          </button>
        </div>
      )}

      <DockedPreview
        memesApi={memesApi}
        member={preview}
        decision={previewDecision}
        onDecide={(d) => preview && setDecision(preview.image_id, d)}
        onMouseEnter={() => { if (previewCloseRef.current) clearTimeout(previewCloseRef.current) }}
        onMouseLeave={closePreviewSoon}
      />

      {peek && (
        <Modal onClose={() => setPeek(null)} title={peek.filename}>
          <img src={memesApi.getImageUrlById(peek.image_id)} alt={peek.filename} className="max-w-full max-h-[80vh] object-contain" />
        </Modal>
      )}
    </div>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div><h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>{children}</div>
}
```

Implementation notes for the executor:
- The old `submitAll`/`submitCluster` awaited `load()` before setting the summary. That's gone — no `load()` on the happy path, so the ordering hazard the old comment described no longer exists.
- `decidedPendingIn` / `isFullyResolved` read `decisions` from closure — fine, `runSubmit` is created fresh each render.
- `useMemo` for the counts intentionally depends on `decisions` (lint-disabled for the helper-closure call) — mirrors the existing pattern the file already uses elsewhere.
- Keep `react-compiler` happy: no ref reads/writes during render.

- [ ] **Step 4: Run the page tests**

Run: `cd Frontend/memes-frontend && npx vitest run src/pages/IngestionReviewPage.test.tsx`
Expected: PASS. Iterate on the component (not the tests) until green. If a Virtuoso-in-jsdom issue blocks item rendering, set `initialItemCount={clusters.length}` on `<Virtuoso>` so all items render in tests (harmless in production — Virtuoso still virtualizes after mount).

- [ ] **Step 5: Full frontend gate**

Run:
```bash
cd Frontend/memes-frontend
npx tsc -b
npx eslint src/
npx vitest run
```
Expected: all three clean (0 eslint warnings).

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "feat: virtualized paginated ingestion review with docked preview and optimistic removal

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

---

## Task 8: Full verification + manual smoke + spec status

**Files:**
- Modify: `docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md` (status line)

- [ ] **Step 1: Backend suite**

Run: `cd Backend && pytest -q`
Expected: PASS. Then: `pytest tests/ -k ingestion -q` from repo root (rules/ai/batch roots are fine to include here since `-k ingestion` won't match their async tests — but if collection errors appear, run `cd Backend && pytest -k ingestion` instead). Expected: PASS.

- [ ] **Step 2: Frontend gate**

Run:
```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/ && npx vitest run
```
Expected: clean.

- [ ] **Step 3: Generated-type drift check**

Run:
```bash
cd Frontend && bash generate-types.sh && cd ..
python AndroidClient/scripts/generate_dtos.py
git diff --stat   # expect NO changes (Task 3 already committed them)
```
Expected: empty diff. If not, commit the regenerated files.

- [ ] **Step 4: Manual smoke (if a `general` ingestion run is active)**

Do NOT bind the always-occupied env ports. Use the dev servers the developer already runs
(`pnpm dev-gen` → :5174, backend :8082). In the browser at `/ingestion`:
- Whole images visible (not cropped), OCR text reads as Russian first, no 3-line clamp.
- Scrolling loads more clusters; "Load more" button works.
- Hovering a tile opens the docked pane fast (no multi-second stall on second hover).
- "Submit all decisions" bar stays fixed while scrolling; submitting removes clusters immediately.
- Backend still healthy: `curl :8082/api/diagnostics/health`, `curl ':8082/api/images?limit=1'`.

If no run is active, note that in the review report and rely on the automated tests.

- [ ] **Step 5: Update spec status**

In `docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md`, change `status: approved` → `status: done` and add `Plan: docs/superpowers/plans/2026-09-07-ingestion-review-ux-overhaul.md` under it.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-09-07-ingestion-review-ux-overhaul-design.md
git commit -m "docs: mark ingestion review UX overhaul spec done

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KD3Wny1CDD3fA8ePbLGMEj"
```

- [ ] **Step 7: Review**

Invoke `superpowers:requesting-code-review` for the whole branch against the spec. One review iteration; fix action points; post the final summary (fixed / deviated-with-reason / intentionally-skipped).

---

## Self-Review (completed during planning)

**Spec coverage:**
- Problem #1 (cropped) → Task 5 (`object-contain`, `max-h-[55vh]`).
- Problem #2 (RU OCR) → Tasks 1–2 (`get_ocr_texts` ordering/gate; service passes thresholds).
- Problem #3 (modal round-trips) → Tasks 5–7 (inline full image + docked pane on hover).
- Problem #4 (no click feedback) → Tasks 5–7 (`active:` states, `transition-colors`, `Submitting…`), root cause via Task 7 virtualization.
- Problem #5 (slow page) → Tasks 3 + 7 (pagination + `<Virtuoso>` + `loading="lazy"`).
- Problem #6 (submit scrolls away) → Task 7 (`fixed` bottom bar).
- Problem #7 (clusters linger) → Task 7 (optimistic removal + rollback).
- Problem #8 (full-size stall) → Tasks 5–7 (near-full-res tile + same URL in pane + `decoding="async"` + hover preload).
- Spec §1 pagination → Tasks 2, 3. Spec §2 OCR → Task 1. Spec §3 API client → Task 4. Spec §4 list → Task 7. Spec §5 hybrid → Tasks 5, 6, 7. Spec §6 bar/optimistic/feedback → Task 7.
- Non-goals respected: no Pillow, no per-language schema, no `wordfreq`, no pipeline changes, no backward windowing.

**Placeholder scan:** No TBD/TODO. Every code step has real code. Test bodies are concrete.

**Type consistency:** `Decision` defined in Task 5 `types.ts`, imported everywhere after. `getIngestionClusters(tier, cursor?)` → `IngestionClusterPage` consistent Tasks 4/7. `list_clusters(tier, batch_id, cursor, limit)` → `{items,next_cursor,has_next}` consistent Tasks 2/3. `get_ocr_texts(image_ids, confidence_min, lang_score_min)` consistent Tasks 1/2. Response field names `next_cursor`/`has_next` (snake_case, from schema) used consistently in TS too (generated types keep snake_case in this repo — verify against an existing generated type like `IngestionResolveResponse`'s `move_failed`).
