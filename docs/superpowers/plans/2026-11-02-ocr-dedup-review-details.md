# OCR and distance details on the /duplicates review page: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each cluster member's OCR text and each member pair's distance and pairing source in a collapsible "Details" panel on `/duplicates`.

**Architecture:** `GET /api/images/duplicates/clusters/{id}/similarity` returns every member pair with its OCR overlap (nullable) and the `tmp_duplicates` candidates (distance, source), plus a `capped` flag. A new frontend hook owns the lazy fetch (with loading, error and retry); a new panel component renders members (from the OCR `text` the list items already carry) and the sorted pair list. Display only, nothing is written.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, React 19 + TypeScript, vitest, shared JSON schemas with three generators.

**Spec:** `docs/superpowers/specs/2026-11-02-ocr-dedup-review-details-design.md`

## Global Constraints

- Display only: no writes, no migration, no setting, no change to detection, thresholds or `clusterize`.
- `SIMILARITY_MEMBER_CAP = 100` stays; over it the response has no pairs.
- Sources shown are `clip` and `ocr_text` only (safety-net rows are stored as `clip`; no third label).
- Every member pair is returned (`candidates: []` for a pair only linked through other members); flagged and non-active members are left out (`get_cluster_member_ids` already filters to `status = 'active'`).
- The "Details" toggle and the retry button are at least 44 px tall (`min-h-11`).
- Frontend rules: effects survive a StrictMode double mount; refs are touched only in handlers and effects; `tsconfig erasableSyntaxOnly` (no constructor parameter properties); React Compiler lint.
- No OCR text in logs; the endpoint keeps `no_cache_headers()`.
- `backend_api.md` stays in sync with the router.
- Python venv: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe` (use forward slashes). Test roots are run separately: `Backend/tests`, `tests/integration` (needs `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test"`), `tests/docs`, `batch/tests`, `tests/rules`. Never bind the always-on ports and never use the main `DATABASE_URL`.

## Review Focus

- A cluster whose members have no OCR text at all: overlap is `null` for every pair, the badges on cards must not crash or show `NaN`, and the panel shows "no text" per member.
- A pair stored in the table in the opposite id order from the one the page lists: still found (the repository normalises the key).
- A pair with both a `clip` and an `ocr_text` row: both chips show, the sort uses the `clip` distance.
- A cluster of exactly 100 members (pairs returned, 4,950 of them) versus 101 (`capped: true`, no pairs): the notice appears only for the second.
- A failed fetch, then retry: the panel leaves the error state and does not leave a stale "requested" flag that blocks the retry.
- Selecting two members (existing behaviour) after the panel was opened: only one request per cluster.

---

### Task 1: Repository and service return full pair details

**Files:**
- Modify: `Backend/app/repositories/image_repository.py` (add `get_cluster_pair_candidates` after `get_cluster_member_ids`, ~line 802)
- Modify: `Backend/app/services/image_service.py` (replace `get_cluster_overlap_coefficients`, ~line 633; add the dataclasses near `SIMILARITY_MEMBER_CAP`, line 50)
- Test: `tests/integration/test_backend_image_repository.py`, `Backend/tests/test_image_service.py`

**Interfaces:**
- Produces:
  - `ImageRepository.get_cluster_pair_candidates(member_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, uuid.UUID], list[tuple[float, str]]]`: key is `(min(a, b), max(a, b))`, value is `(distance, distance_source)` tuples sorted by distance ascending. Only rows with both ids in `member_ids`.
  - `ClusterPairDetail(image_id1: uuid.UUID, image_id2: uuid.UUID, overlap: float | None, candidates: list[tuple[float, str]])` (dataclass).
  - `ClusterPairDetails(pairs: list[ClusterPairDetail], capped: bool)` (dataclass).
  - `ImageService.get_cluster_pair_details(cluster_id: int) -> ClusterPairDetails`. It replaces `get_cluster_overlap_coefficients`. Over the cap: `ClusterPairDetails(pairs=[], capped=True)` and neither repository is queried for pairs. Otherwise one `ClusterPairDetail` per combination of members, ordered by `(image_id1, image_id2)`, `image_id1 < image_id2`.

- [ ] **Step 1: Write the failing repository tests**

Append to `tests/integration/test_backend_image_repository.py` (imports `Image`, `TmpDuplicates`, `ImageRepository`, `uuid`, `pytest` already exist there):

```python
@pytest.mark.asyncio(loop_scope="session")
async def test_get_cluster_pair_candidates_returns_every_row_sorted_by_distance(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg")
    b = Image(filename=f"{uuid.uuid4()}.jpg")
    c = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b, c])
    await db_session.flush()
    low, high = sorted([a.id, b.id])
    db_session.add(TmpDuplicates(image_id1=low, image_id2=high, distance=0.08, distance_source="ocr_text"))
    await db_session.flush()
    repo = ImageRepository(db_session)

    result = await repo.get_cluster_pair_candidates([a.id, b.id, c.id])

    assert result == {(low, high): [(0.08, "ocr_text")]}


@pytest.mark.asyncio(loop_scope="session")
async def test_get_cluster_pair_candidates_ignores_pairs_outside_the_member_list(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg")
    b = Image(filename=f"{uuid.uuid4()}.jpg")
    outsider = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b, outsider])
    await db_session.flush()
    low, high = sorted([a.id, outsider.id])
    db_session.add(TmpDuplicates(image_id1=low, image_id2=high, distance=0.03))
    await db_session.flush()
    repo = ImageRepository(db_session)

    assert await repo.get_cluster_pair_candidates([a.id, b.id]) == {}


@pytest.mark.asyncio(loop_scope="session")
async def test_get_cluster_pair_candidates_normalises_the_key_order(db_session):
    a = Image(filename=f"{uuid.uuid4()}.jpg")
    b = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b])
    await db_session.flush()
    low, high = sorted([a.id, b.id])
    db_session.add(TmpDuplicates(image_id1=low, image_id2=high, distance=0.02))
    await db_session.flush()
    repo = ImageRepository(db_session)

    # Asking with the members in the opposite order finds the same single key.
    assert list(await repo.get_cluster_pair_candidates([high, low])) == [(low, high)]


@pytest.mark.asyncio(loop_scope="session")
async def test_get_cluster_pair_candidates_empty_and_single_member(db_session):
    repo = ImageRepository(db_session)
    assert await repo.get_cluster_pair_candidates([]) == {}
    assert await repo.get_cluster_pair_candidates([uuid.uuid4()]) == {}
```

`uq_tmp_duplicates_pair` is on `(image_id1, image_id2)` (see `Storage/models.py`), so a pair has one stored row in practice; the list shape stays because the spec allows several, and the service test covers the multi-candidate case with a mocked repository.

- [ ] **Step 2: Run to verify they fail**

Run: `cd /h/workspace_sandbox/memes && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration/test_backend_image_repository.py -k cluster_pair_candidates -v`
Expected: FAIL with `AttributeError: 'ImageRepository' object has no attribute 'get_cluster_pair_candidates'`.

- [ ] **Step 3: Implement the repository method**

In `Backend/app/repositories/image_repository.py`, after `get_cluster_member_ids`:

```python
    async def get_cluster_pair_candidates(
            self, member_ids: list[uuid.UUID]
    ) -> dict[tuple[uuid.UUID, uuid.UUID], list[tuple[float, str]]]:
        """Every tmp_duplicates row between two of member_ids, keyed by (min id, max id), as
        (distance, distance_source) sorted by distance. Presentation-only; a pair that was
        only linked through other members has no row and so no key."""
        if len(member_ids) < 2:
            return {}
        rows = await self.session.execute(
            select(
                TmpDuplicates.image_id1, TmpDuplicates.image_id2,
                TmpDuplicates.distance, TmpDuplicates.distance_source,
            )
            .where(TmpDuplicates.image_id1.in_(member_ids), TmpDuplicates.image_id2.in_(member_ids))
            .order_by(TmpDuplicates.distance)
        )
        result: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[float, str]]] = {}
        for id1, id2, distance, source in rows:
            result.setdefault((min(id1, id2), max(id1, id2)), []).append((distance, source))
        return result
```

Add `TmpDuplicates` to the file's existing `Storage.models` import if it is not there.

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 command. Expected: 4 passed.

- [ ] **Step 5: Write the failing service tests**

In `Backend/tests/test_image_service.py` replace the whole `TestClusterOverlapCoefficients` class with:

```python
class TestClusterPairDetails:
    async def test_returns_every_member_pair_with_overlap_and_candidates(self, service, mock_repo, mock_ocr_lemmas_repo):
        a, b, c = sorted([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
        mock_repo.get_cluster_member_ids.return_value = [c, a, b]
        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.return_value = {(a, b): 0.5}
        mock_repo.get_cluster_pair_candidates.return_value = {
            (a, b): [(0.03, "clip"), (0.08, "ocr_text")],
        }

        result = await service.get_cluster_pair_details(141)

        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.assert_awaited_once_with([c, a, b])
        mock_repo.get_cluster_pair_candidates.assert_awaited_once_with([c, a, b])
        assert result.capped is False
        assert [(p.image_id1, p.image_id2) for p in result.pairs] == [(a, b), (a, c), (b, c)]
        assert result.pairs[0].overlap == 0.5
        assert result.pairs[0].candidates == [(0.03, "clip"), (0.08, "ocr_text")]
        # No lemmas and no tmp_duplicates row: still returned, with nothing to show.
        assert result.pairs[1].overlap is None
        assert result.pairs[1].candidates == []

    async def test_skips_computation_above_the_member_cap(self, service, mock_repo, mock_ocr_lemmas_repo):
        from Backend.app.services.image_service import SIMILARITY_MEMBER_CAP
        mock_repo.get_cluster_member_ids.return_value = [uuid.uuid4() for _ in range(SIMILARITY_MEMBER_CAP + 1)]

        result = await service.get_cluster_pair_details(141)

        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.assert_not_awaited()
        mock_repo.get_cluster_pair_candidates.assert_not_awaited()
        assert result.pairs == []
        assert result.capped is True

    async def test_exactly_at_the_cap_still_returns_pairs(self, service, mock_repo, mock_ocr_lemmas_repo):
        from Backend.app.services.image_service import SIMILARITY_MEMBER_CAP
        mock_repo.get_cluster_member_ids.return_value = [uuid.uuid4() for _ in range(SIMILARITY_MEMBER_CAP)]
        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.return_value = {}
        mock_repo.get_cluster_pair_candidates.return_value = {}

        result = await service.get_cluster_pair_details(141)

        assert result.capped is False
        assert len(result.pairs) == SIMILARITY_MEMBER_CAP * (SIMILARITY_MEMBER_CAP - 1) // 2

    async def test_single_member_cluster_has_no_pairs(self, service, mock_repo, mock_ocr_lemmas_repo):
        mock_repo.get_cluster_member_ids.return_value = [uuid.uuid4()]
        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.return_value = {}
        mock_repo.get_cluster_pair_candidates.return_value = {}

        result = await service.get_cluster_pair_details(141)

        assert result.pairs == []
        assert result.capped is False
```

- [ ] **Step 6: Run to verify they fail**

Run: `cd /h/workspace_sandbox/memes/Backend && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/test_image_service.py -k ClusterPairDetails -v`
Expected: FAIL with `AttributeError: ... 'get_cluster_pair_details'`.

- [ ] **Step 7: Implement the service method**

In `Backend/app/services/image_service.py`: add `from dataclasses import dataclass` and `from itertools import combinations` if absent, then below `SIMILARITY_MEMBER_CAP = 100`:

```python
@dataclass
class ClusterPairDetail:
    image_id1: uuid.UUID
    image_id2: uuid.UUID
    overlap: float | None
    candidates: list[tuple[float, str]]


@dataclass
class ClusterPairDetails:
    pairs: list[ClusterPairDetail]
    capped: bool
```

Replace `get_cluster_overlap_coefficients` with:

```python
    async def get_cluster_pair_details(self, cluster_id: int) -> ClusterPairDetails:
        member_ids = await self.repo.get_cluster_member_ids(cluster_id)
        if len(member_ids) > SIMILARITY_MEMBER_CAP:
            return ClusterPairDetails(pairs=[], capped=True)
        overlaps = await self.ocr_lemmas_repo.get_pairwise_overlap_coefficients(member_ids)
        candidates = await self.repo.get_cluster_pair_candidates(member_ids)
        pairs = [
            ClusterPairDetail(a, b, overlaps.get((a, b)), candidates.get((a, b), []))
            for a, b in combinations(sorted(member_ids), 2)
        ]
        return ClusterPairDetails(pairs=pairs, capped=False)
```

- [ ] **Step 8: Run to verify they pass, then commit**

Run: `cd /h/workspace_sandbox/memes/Backend && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/test_image_service.py -v`
Expected: all pass. (`tests/test_images_endpoints.py::TestClusterSimilarity` now fails because the router still calls the removed method; Task 2 fixes it.)

```bash
git add Backend/app/repositories/image_repository.py Backend/app/services/image_service.py tests/integration/test_backend_image_repository.py Backend/tests/test_image_service.py
git commit -m "feat(dedup): cluster pair details in the image service"
```

---

### Task 2: Schema, router, generated types and API docs

**Files:**
- Create: `shared/schemas/clustersimilaritycandidate.schema.json`
- Modify: `shared/schemas/clustersimilaritypair.schema.json`, `shared/schemas/clustersimilarityresponse.schema.json`, `shared/schemas/all.schema.json`
- Modify: `Backend/app/api/images.py` (models at lines 71-78, route at 363-376)
- Modify: `backend_api.md` (the cluster similarity section; find it with `grep -n "similarity" backend_api.md`)
- Regenerated: `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`, `Backend/app/types/generated/*`
- Test: `Backend/tests/test_images_endpoints.py` (`TestClusterSimilarity`, line 1588)

**Interfaces:**
- Consumes: `ImageService.get_cluster_pair_details(cluster_id) -> ClusterPairDetails` (Task 1).
- Produces (wire): `{ "pairs": [{ "image_id1", "image_id2", "overlap": number|null, "candidates": [{ "distance": number, "source": "clip"|"ocr_text" }] }], "capped": boolean }`. Generated TS types `ClusterSimilarityPair`, `ClusterSimilarityCandidate`, `ClusterSimilarityResponse` (Task 3 consumes these names).

- [ ] **Step 1: Write the failing endpoint tests**

Replace the body of `TestClusterSimilarity` in `Backend/tests/test_images_endpoints.py` (add `from Backend.app.services.image_service import ClusterPairDetail, ClusterPairDetails` to the imports):

```python
class TestClusterSimilarity:
    """Tests for GET /api/images/duplicates/clusters/{cluster_id}/similarity."""

    A = uuid.UUID("11111111-1111-1111-1111-111111111111")
    B = uuid.UUID("22222222-2222-2222-2222-222222222222")

    def test_similarity_success(self, client, mock_image_service):
        mock_image_service.get_cluster_pair_details.return_value = ClusterPairDetails(
            pairs=[ClusterPairDetail(self.A, self.B, 0.75, [(0.03, "clip"), (0.08, "ocr_text")])],
            capped=False,
        )

        response = client.get("/api/images/duplicates/clusters/141/similarity")

        assert response.status_code == 200
        assert response.json() == {
            "pairs": [{
                "image_id1": str(self.A), "image_id2": str(self.B), "overlap": 0.75,
                "candidates": [
                    {"distance": 0.03, "source": "clip"},
                    {"distance": 0.08, "source": "ocr_text"},
                ],
            }],
            "capped": False,
        }
        mock_image_service.get_cluster_pair_details.assert_awaited_once_with(141)

    def test_null_overlap_and_no_candidates(self, client, mock_image_service):
        mock_image_service.get_cluster_pair_details.return_value = ClusterPairDetails(
            pairs=[ClusterPairDetail(self.A, self.B, None, [])], capped=False,
        )

        data = client.get("/api/images/duplicates/clusters/141/similarity").json()

        assert data["pairs"][0]["overlap"] is None
        assert data["pairs"][0]["candidates"] == []

    def test_capped_cluster_has_no_pairs(self, client, mock_image_service):
        mock_image_service.get_cluster_pair_details.return_value = ClusterPairDetails(pairs=[], capped=True)

        data = client.get("/api/images/duplicates/clusters/141/similarity").json()

        assert data == {"pairs": [], "capped": True}

    def test_response_is_not_cached(self, client, mock_image_service):
        mock_image_service.get_cluster_pair_details.return_value = ClusterPairDetails(pairs=[], capped=False)

        response = client.get("/api/images/duplicates/clusters/141/similarity")

        assert "no-store" in response.headers["cache-control"]
```

Check the exact `no_cache_headers()` value first (`grep -n "def no_cache_headers" -A6 -r Backend/app`) and assert the header it really sets; adjust `"no-store"` if it uses a different directive. Check how the `mock_image_service` fixture is built; if it is `MagicMock` with `AsyncMock` methods set per test, mirror the existing tests (`return_value` on an async method), and replace `assert_awaited_once_with` with `assert_called_once_with` if the fixture uses plain mocks.

- [ ] **Step 2: Run to verify they fail**

Run: `cd /h/workspace_sandbox/memes/Backend && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/test_images_endpoints.py -k TestClusterSimilarity -v`
Expected: FAIL (the router still calls `get_cluster_overlap_coefficients`).

- [ ] **Step 3: Edit the shared schemas**

Create `shared/schemas/clustersimilaritycandidate.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "clustersimilaritycandidate.schema.json",
  "title": "ClusterSimilarityCandidate",
  "type": "object",
  "properties": {
    "distance": { "type": "number", "description": "Embedding distance of the stored candidate pair. Lower is closer." },
    "source": { "type": "string", "enum": ["clip", "ocr_text"], "description": "Which embedding signal produced the pair: CLIP image vectors or OCR-text vectors. Safety-net rows are stored as clip." }
  },
  "required": ["distance", "source"]
}
```

Replace `shared/schemas/clustersimilaritypair.schema.json` with:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "clustersimilaritypair.schema.json",
  "title": "ClusterSimilarityPair",
  "type": "object",
  "properties": {
    "image_id1": { "type": "string" },
    "image_id2": { "type": "string" },
    "overlap": { "type": ["number", "null"], "description": "OCR-lemma overlap coefficient, 0.0-1.0; null when either image has no OCR lemmas. Presentation-only, never written anywhere." },
    "candidates": { "type": "array", "items": { "$ref": "./clustersimilaritycandidate.schema.json" }, "description": "The stored candidate rows for this pair, closest first. Empty when the two images are only linked through other members of the cluster." }
  },
  "required": ["image_id1", "image_id2", "overlap", "candidates"]
}
```

Replace `shared/schemas/clustersimilarityresponse.schema.json` with:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "clustersimilarityresponse.schema.json",
  "title": "ClusterSimilarityResponse",
  "type": "object",
  "properties": {
    "pairs": { "type": "array", "items": { "$ref": "./clustersimilaritypair.schema.json" } },
    "capped": { "type": "boolean", "description": "True when the cluster has more members than the pair limit (100); pairs is then empty." }
  },
  "required": ["pairs", "capped"]
}
```

In `shared/schemas/all.schema.json`, add `"ClusterSimilarityCandidate": { "$ref": "clustersimilaritycandidate.schema.json" },` directly above the `ClusterSimilarityPair` line (keep the existing alignment style).

- [ ] **Step 4: Edit the router**

In `Backend/app/api/images.py` replace the two models and the route:

```python
class ClusterSimilarityCandidateModel(BaseModel):
    distance: float
    source: Literal["clip", "ocr_text"]


class ClusterSimilarityPairModel(BaseModel):
    image_id1: str
    image_id2: str
    overlap: float | None
    candidates: list[ClusterSimilarityCandidateModel]


class ClusterSimilarityResponseModel(BaseModel):
    pairs: list[ClusterSimilarityPairModel]
    capped: bool
```

```python
    response.headers.update(no_cache_headers())
    details = await service.get_cluster_pair_details(cluster_id)
    return ClusterSimilarityResponseModel(
        pairs=[
            ClusterSimilarityPairModel(
                image_id1=str(pair.image_id1),
                image_id2=str(pair.image_id2),
                overlap=pair.overlap,
                candidates=[
                    ClusterSimilarityCandidateModel(distance=distance, source=source)
                    for distance, source in pair.candidates
                ],
            )
            for pair in details.pairs
        ],
        capped=details.capped,
    )
```

Add `Literal` to the file's `typing` import if missing. The `source` Literal makes an unexpected `distance_source` value a 500 rather than a silent pass-through; that matches the schema enum and the two values the batch jobs write.

- [ ] **Step 5: Run to verify they pass**

Run the Step 2 command. Expected: 4 passed. Then run the whole root: `cd /h/workspace_sandbox/memes/Backend && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest -q`. Expected: all pass.

- [ ] **Step 6: Regenerate the types**

From `Frontend/`: `bash generate-types.sh`. Android: `H:/workspace_sandbox/memes/.venv311/Scripts/python.exe AndroidClient/scripts/generate_dtos.py` (from the repo root). Python: the `datamodel-codegen` command in `documents/generation.md`, run from `Backend/`. It rewrites every generated file; keep only the changed ones: copy `Backend/app/types/generated/__init__.py` aside first, run the generator, then `git checkout -- Backend/app/types/generated` for everything that is not `clustersimilarity*.py`, and put `__init__.py` back with the new `clustersimilaritycandidate` import added by hand if the generator changed it. Check with `git status`: only the three clustersimilarity-related Python files (plus `__init__.py`), `all.d.ts` and `Models.kt` may differ, and `Models.kt` must show `overlap: Double?`, a `candidates` list and `capped`.

- [ ] **Step 7: Update `backend_api.md`**

Find the cluster similarity section (`grep -n "clusters/{" backend_api.md`) and replace its response description with the new shape: `pairs` (every pair of active cluster members, `image_id1 < image_id2`), per pair `overlap` (number or null when either image has no OCR lemmas), `candidates` (list of `{distance, source}` with `source` `clip` or `ocr_text`, closest first, empty when the pair is only linked through other members), and `capped` (true and `pairs` empty when the cluster has more than 100 members). Keep the note that it is read-only and presentation-only.

- [ ] **Step 8: Commit**

```bash
git add -A :/
git status --short
git commit -m "feat(dedup): similarity endpoint returns candidates, nullable overlap and capped flag"
```
(`git status --short` must list only the files named in this task.)

---

### Task 3: Frontend data layer (hook and pair helpers)

**Files:**
- Create: `Frontend/memes-frontend/src/duplicates/pairs.ts`, `pairs.test.ts`, `useClusterSimilarity.ts`, `useClusterSimilarity.test.ts`
- Test fixtures: add `makeSimilarityPair` and `makeSimilarityResponse` to `Frontend/memes-frontend/src/duplicates/fixtures.ts` (create)
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts` (line 51: `getClusterSimilarity: vi.fn().mockResolvedValue({ pairs: [], capped: false })`)

**Interfaces:**
- Consumes: generated types `ClusterSimilarityPair`, `ClusterSimilarityCandidate`, `ClusterSimilarityResponse` (Task 2); `MemesApi.getClusterSimilarity(clusterId): Promise<ClusterSimilarityResponse>`.
- Produces:
  - `sortPairs(pairs: ClusterSimilarityPair[]): ClusterSimilarityPair[]`: returns a new array. Pairs with a `clip` candidate first, by smallest `clip` distance; then pairs with only an `ocr_text` candidate by smallest distance; then pairs with no candidates; ties by `image_id1` then `image_id2`.
  - `bestClipDistance(pair): number | undefined`.
  - `overlapPercent(overlap: number | null): string | null`: `"75%"`, `null` for null.
  - `SimilarityEntry = { status: 'loading' } | { status: 'error' } | { status: 'ok'; pairs: ClusterSimilarityPair[]; capped: boolean }`.
  - `useClusterSimilarity(memesApi: MemesApi): { entries: Map<number, SimilarityEntry>; load: (clusterId: number) => void; retry: (clusterId: number) => void }`. `load` is a no-op if that cluster was already requested; `retry` clears the cluster's request and fetches again. Both are called from event handlers only.

- [ ] **Step 1: Write the failing tests for the pure helpers**

`Frontend/memes-frontend/src/duplicates/fixtures.ts`:

```ts
import type { ClusterSimilarityPair, ClusterSimilarityResponse } from '../types/generated/all'

export function makeSimilarityPair(overrides: Partial<ClusterSimilarityPair> = {}): ClusterSimilarityPair {
  return { image_id1: 'a', image_id2: 'b', overlap: 0.5, candidates: [], ...overrides }
}

export function makeSimilarityResponse(overrides: Partial<ClusterSimilarityResponse> = {}): ClusterSimilarityResponse {
  return { pairs: [], capped: false, ...overrides }
}
```

`pairs.test.ts`:

```ts
import { bestClipDistance, overlapPercent, sortPairs } from './pairs'
import { makeSimilarityPair } from './fixtures'

describe('sortPairs', () => {
  it('puts the closest clip distance first', () => {
    const far = makeSimilarityPair({ image_id1: 'a', image_id2: 'b', candidates: [{ distance: 0.09, source: 'clip' }] })
    const near = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', candidates: [{ distance: 0.02, source: 'clip' }] })
    expect(sortPairs([far, near])).toEqual([near, far])
  })

  it('puts pairs without a clip candidate after those with one, ocr_text before none', () => {
    const none = makeSimilarityPair({ image_id1: 'a', image_id2: 'b' })
    const text = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', candidates: [{ distance: 0.01, source: 'ocr_text' }] })
    const clip = makeSimilarityPair({ image_id1: 'b', image_id2: 'c', candidates: [{ distance: 0.9, source: 'clip' }] })
    expect(sortPairs([none, text, clip])).toEqual([clip, text, none])
  })

  it('uses the clip distance when a pair also has an ocr_text candidate', () => {
    const both = makeSimilarityPair({ candidates: [{ distance: 0.08, source: 'ocr_text' }, { distance: 0.04, source: 'clip' }] })
    expect(bestClipDistance(both)).toBe(0.04)
  })

  it('breaks ties by ids and does not mutate the input', () => {
    const second = makeSimilarityPair({ image_id1: 'b', image_id2: 'c' })
    const first = makeSimilarityPair({ image_id1: 'a', image_id2: 'z' })
    const input = [second, first]
    expect(sortPairs(input)).toEqual([first, second])
    expect(input).toEqual([second, first])
  })
})

describe('overlapPercent', () => {
  it('formats a coefficient as a whole percent', () => {
    expect(overlapPercent(0.75)).toBe('75%')
    expect(overlapPercent(0)).toBe('0%')
  })

  it('returns null for a missing overlap, never NaN', () => {
    expect(overlapPercent(null)).toBeNull()
  })
})
```

- [ ] **Step 2: Run to verify they fail**

Run (from `Frontend/memes-frontend/`): `pnpm exec vitest run src/duplicates/pairs.test.ts`
Expected: FAIL, cannot resolve `./pairs`.

- [ ] **Step 3: Implement `pairs.ts`**

```ts
import type { ClusterSimilarityPair } from '../types/generated/all'

export function bestClipDistance(pair: ClusterSimilarityPair): number | undefined {
  const clip = pair.candidates.filter(c => c.source === 'clip').map(c => c.distance)
  return clip.length > 0 ? Math.min(...clip) : undefined
}

function bestOtherDistance(pair: ClusterSimilarityPair): number | undefined {
  const all = pair.candidates.map(c => c.distance)
  return all.length > 0 ? Math.min(...all) : undefined
}

function rank(pair: ClusterSimilarityPair): [number, number] {
  const clip = bestClipDistance(pair)
  if (clip !== undefined) return [0, clip]
  const other = bestOtherDistance(pair)
  if (other !== undefined) return [1, other]
  return [2, 0]
}

export function sortPairs(pairs: ClusterSimilarityPair[]): ClusterSimilarityPair[] {
  return [...pairs].sort((p, q) => {
    const [pg, pd] = rank(p)
    const [qg, qd] = rank(q)
    if (pg !== qg) return pg - qg
    if (pd !== qd) return pd - qd
    return p.image_id1.localeCompare(q.image_id1) || p.image_id2.localeCompare(q.image_id2)
  })
}

export function overlapPercent(overlap: number | null): string | null {
  return overlap === null ? null : `${Math.round(overlap * 100)}%`
}
```

- [ ] **Step 4: Run to verify they pass**

Run the Step 2 command. Expected: 6 passed.

- [ ] **Step 5: Write the failing hook tests**

`useClusterSimilarity.test.ts`:

```ts
import { act, renderHook, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { makeMockApi } from '../test/mockApi'
import { makeSimilarityPair, makeSimilarityResponse } from './fixtures'
import { useClusterSimilarity } from './useClusterSimilarity'

describe('useClusterSimilarity', () => {
  it('starts empty and fetches nothing on mount', () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useClusterSimilarity(api), { wrapper: StrictMode })
    expect(result.current.entries.size).toBe(0)
    expect(api.getClusterSimilarity).not.toHaveBeenCalled()
  })

  it('load marks the cluster loading, then ok with the response', async () => {
    const response = makeSimilarityResponse({ pairs: [makeSimilarityPair()] })
    const api = makeMockApi({ getClusterSimilarity: vi.fn().mockResolvedValue(response) })
    const { result } = renderHook(() => useClusterSimilarity(api))

    act(() => result.current.load(7))
    expect(result.current.entries.get(7)).toEqual({ status: 'loading' })
    await waitFor(() => expect(result.current.entries.get(7)).toEqual({ status: 'ok', pairs: response.pairs, capped: false }))
    expect(api.getClusterSimilarity).toHaveBeenCalledWith(7)
  })

  it('requests a cluster only once however often load is called', async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useClusterSimilarity(api), { wrapper: StrictMode })

    act(() => { result.current.load(7); result.current.load(7) })
    act(() => result.current.load(7))

    await waitFor(() => expect(result.current.entries.get(7)?.status).toBe('ok'))
    expect(api.getClusterSimilarity).toHaveBeenCalledTimes(1)
  })

  it('keeps the capped flag', async () => {
    const api = makeMockApi({ getClusterSimilarity: vi.fn().mockResolvedValue(makeSimilarityResponse({ capped: true })) })
    const { result } = renderHook(() => useClusterSimilarity(api))
    act(() => result.current.load(3))
    await waitFor(() => expect(result.current.entries.get(3)).toEqual({ status: 'ok', pairs: [], capped: true }))
  })

  it('a failed fetch ends in error and retry fetches again', async () => {
    const getClusterSimilarity = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(makeSimilarityResponse())
    const api = makeMockApi({ getClusterSimilarity })
    const { result } = renderHook(() => useClusterSimilarity(api))

    act(() => result.current.load(5))
    await waitFor(() => expect(result.current.entries.get(5)).toEqual({ status: 'error' }))

    act(() => result.current.retry(5))
    expect(result.current.entries.get(5)).toEqual({ status: 'loading' })
    await waitFor(() => expect(result.current.entries.get(5)?.status).toBe('ok'))
    expect(getClusterSimilarity).toHaveBeenCalledTimes(2)
  })

  it('keeps clusters independent', async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useClusterSimilarity(api))
    act(() => { result.current.load(1); result.current.load(2) })
    await waitFor(() => expect(result.current.entries.get(2)?.status).toBe('ok'))
    expect(result.current.entries.get(1)?.status).toBe('ok')
    expect(api.getClusterSimilarity).toHaveBeenCalledTimes(2)
  })
})
```

Note on the StrictMode case: `load` is called from the test, not from an effect, so StrictMode's double mount cannot double the request; the test pins that the hook has no mount-time fetch to double.

- [ ] **Step 6: Run to verify they fail**

Run: `pnpm exec vitest run src/duplicates/useClusterSimilarity.test.ts`
Expected: FAIL, cannot resolve `./useClusterSimilarity`.

- [ ] **Step 7: Implement the hook and update the mock**

`useClusterSimilarity.ts`:

```ts
import { useCallback, useRef, useState } from 'react'
import type { MemesApi } from '../api/MemesApi'
import type { ClusterSimilarityPair } from '../types/generated/all'

export type SimilarityEntry =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ok'; pairs: ClusterSimilarityPair[]; capped: boolean }

// Lazy per-cluster similarity data for the /duplicates page. `load` and `retry` are called from
// event handlers only (never from an effect), so StrictMode's double mount cannot double a request,
// and the ref is touched only inside those handlers.
export function useClusterSimilarity(memesApi: MemesApi) {
  const [entries, setEntries] = useState<Map<number, SimilarityEntry>>(new Map())
  const requestedRef = useRef<Set<number>>(new Set())

  const fetchCluster = useCallback((clusterId: number) => {
    requestedRef.current.add(clusterId)
    setEntries(prev => new Map(prev).set(clusterId, { status: 'loading' }))
    memesApi.getClusterSimilarity(clusterId).then(
      response => setEntries(prev => new Map(prev).set(clusterId, { status: 'ok', pairs: response.pairs, capped: response.capped })),
      () => setEntries(prev => new Map(prev).set(clusterId, { status: 'error' })),
    )
  }, [memesApi])

  const load = useCallback((clusterId: number) => {
    if (requestedRef.current.has(clusterId)) return
    fetchCluster(clusterId)
  }, [fetchCluster])

  const retry = useCallback((clusterId: number) => fetchCluster(clusterId), [fetchCluster])

  return { entries, load, retry }
}
```

In `mockApi.ts` change the `getClusterSimilarity` default to `vi.fn().mockResolvedValue({ pairs: [], capped: false })`.

- [ ] **Step 8: Run to verify they pass, check types, commit**

Run: `pnpm exec vitest run src/duplicates` then `pnpm exec tsc -b`.
Expected: 12 passed; tsc clean. (`MemesDuplicatesList.tsx` still compiles: it reads `pair.overlap`, now `number | null`, and `map.set(..., pair.overlap)` into a `Map<string, number>` fails to type-check. Fix it minimally in this task by skipping null overlaps: `if (pair.overlap !== null) map.set(...)`, then re-run `tsc -b` and `pnpm exec vitest run src/components/MemesDuplicatesList.test.tsx`.)

```bash
git add -A :/
git commit -m "feat(dedup): pair helpers and lazy cluster similarity hook"
```

---

### Task 4: The details panel component

**Files:**
- Create: `Frontend/memes-frontend/src/components/duplicates/ClusterDetailsPanel.tsx`, `ClusterDetailsPanel.test.tsx`

**Interfaces:**
- Consumes: `SimilarityEntry` (Task 3), `sortPairs`, `overlapPercent` (Task 3), `MemesApi.getImageUrl(meme)`, `Meme` (generated type, `text: string[]` entries look like `"some words (0.93)"`).
- Produces: `ClusterDetailsPanel({ members, entry, memesApi, onRetry }: { members: Meme[]; entry: SimilarityEntry | undefined; memesApi: MemesApi; onRetry: () => void })`. Pure presentational; it fetches nothing.

- [ ] **Step 1: Write the failing tests**

`ClusterDetailsPanel.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from '@testing-library/react'
import { makeMockApi } from '../../test/mockApi'
import { makeSimilarityPair } from '../../duplicates/fixtures'
import type { Meme } from '../../types/generated/all'
import { ClusterDetailsPanel } from './ClusterDetailsPanel'

function member(id: string, text: string[] = []): Meme {
  return { id, imageUrl: `/api/images/${id}`, text, tags: [], originalFileName: `${id}.jpg`, clusterId: 1 }
}

const api = makeMockApi()

describe('ClusterDetailsPanel', () => {
  it('shows each member with its OCR text, or "no text"', () => {
    render(<ClusterDetailsPanel members={[member('a', ['hello there (0.93)']), member('b')]} entry={{ status: 'ok', pairs: [], capped: false }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText('a.jpg')).toBeInTheDocument()
    expect(screen.getByText('hello there (0.93)')).toBeInTheDocument()
    expect(screen.getByText('no text')).toBeInTheDocument()
  })

  it('shows a loading note while the pairs load', () => {
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'loading' }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText(/loading pair details/i)).toBeInTheDocument()
  })

  it('shows an error with a retry button of at least 44px height', () => {
    const onRetry = vi.fn()
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'error' }} memesApi={api} onRetry={onRetry} />)
    const button = screen.getByRole('button', { name: /retry/i })
    expect(button.className).toContain('min-h-11')
    fireEvent.click(button)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('lists pairs closest clip distance first with chips and overlap', () => {
    const far = makeSimilarityPair({ image_id1: 'a', image_id2: 'b', overlap: 0.2, candidates: [{ distance: 0.09, source: 'clip' }] })
    const near = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', overlap: 0.75, candidates: [{ distance: 0.031, source: 'clip' }, { distance: 0.082, source: 'ocr_text' }] })
    render(<ClusterDetailsPanel members={[member('a'), member('b'), member('c')]} entry={{ status: 'ok', pairs: [far, near], capped: false }} memesApi={api} onRetry={() => {}} />)
    const rows = screen.getAllByRole('listitem', { name: /pair/i })
    expect(within(rows[0]).getByText('clip 0.031')).toBeInTheDocument()
    expect(within(rows[0]).getByText('ocr_text 0.082')).toBeInTheDocument()
    expect(within(rows[0]).getByText(/75%/)).toBeInTheDocument()
    expect(within(rows[1]).getByText('clip 0.090')).toBeInTheDocument()
  })

  it('says a pair is linked through other members when it has no candidates', () => {
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'ok', pairs: [makeSimilarityPair({ overlap: null })], capped: false }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText(/linked through other members/i)).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
    expect(screen.getByText(/no OCR overlap data/i)).toBeInTheDocument()
  })

  it('shows the over-cap notice but still lists members', () => {
    render(<ClusterDetailsPanel members={[member('a', ['kept text'])]} entry={{ status: 'ok', pairs: [], capped: true }} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText(/pair details are unavailable for clusters over 100/i)).toBeInTheDocument()
    expect(screen.getByText('kept text')).toBeInTheDocument()
  })

  it('does not show the over-cap notice for an uncapped response', () => {
    render(<ClusterDetailsPanel members={[member('a'), member('b')]} entry={{ status: 'ok', pairs: [], capped: false }} memesApi={api} onRetry={() => {}} />)
    expect(screen.queryByText(/over 100/i)).not.toBeInTheDocument()
  })

  it('renders members only when there is no entry yet', () => {
    render(<ClusterDetailsPanel members={[member('a', ['x'])]} entry={undefined} memesApi={api} onRetry={() => {}} />)
    expect(screen.getByText('x')).toBeInTheDocument()
    expect(screen.queryByText(/loading pair details/i)).not.toBeInTheDocument()
  })
})
```

Distances are formatted with `toFixed(3)`. Pair rows are `<li aria-label="Pair a.jpg and b.jpg">`-style items so the role query by name works; make the label start with "Pair". `mockApi.getImageUrl` must exist as a `vi.fn` (check `mockApi.ts`; if it returns undefined the `<img src>` is empty, which is fine for these tests).

- [ ] **Step 2: Run to verify they fail**

Run: `pnpm exec vitest run src/components/duplicates/ClusterDetailsPanel.test.tsx`
Expected: FAIL, cannot resolve `./ClusterDetailsPanel`.

- [ ] **Step 3: Implement the panel**

```tsx
import type { MemesApi } from '../../api/MemesApi'
import type { Meme } from '../../types/generated/all'
import { overlapPercent, sortPairs } from '../../duplicates/pairs'
import type { SimilarityEntry } from '../../duplicates/useClusterSimilarity'

type Props = {
  members: Meme[]
  entry: SimilarityEntry | undefined
  memesApi: MemesApi
  onRetry: () => void
}

export function ClusterDetailsPanel({ members, entry, memesApi, onRetry }: Props) {
  const byId = new Map(members.map(m => [m.id, m]))
  const nameOf = (id: string) => byId.get(id)?.originalFileName ?? id.slice(0, 8)

  return (
    <div className="mt-2 rounded border border-gray-200 p-3 text-sm space-y-4">
      <ul className="space-y-2">
        {members.map(meme => (
          <li key={meme.id} className="flex gap-3">
            <img src={memesApi.getImageUrl(meme)} alt="" className="h-16 w-16 object-cover rounded shrink-0" />
            <div className="min-w-0">
              <div className="font-medium break-all">{nameOf(meme.id)}</div>
              {(meme.text ?? []).length > 0
                ? (meme.text ?? []).map((line, i) => <div key={i} className="text-gray-700 break-words">{line}</div>)
                : <div className="text-gray-400 italic">no text</div>}
            </div>
          </li>
        ))}
      </ul>

      {entry?.status === 'loading' && <div className="text-gray-500">Loading pair details...</div>}

      {entry?.status === 'error' && (
        <div className="flex items-center gap-3">
          <span className="text-red-700">Could not load pair details.</span>
          <button className="min-h-11 rounded bg-gray-100 px-4 hover:bg-gray-200" onClick={onRetry}>Retry</button>
        </div>
      )}

      {entry?.status === 'ok' && entry.capped && (
        <div className="text-gray-500">Pair details are unavailable for clusters over 100 members.</div>
      )}

      {entry?.status === 'ok' && !entry.capped && (
        <ul className="space-y-2">
          {sortPairs(entry.pairs).map(pair => {
            const percent = overlapPercent(pair.overlap)
            return (
              <li key={`${pair.image_id1}:${pair.image_id2}`} aria-label={`Pair ${nameOf(pair.image_id1)} and ${nameOf(pair.image_id2)}`} className="flex flex-wrap items-center gap-2">
                <span className="break-all">{nameOf(pair.image_id1)} / {nameOf(pair.image_id2)}</span>
                <span className="text-gray-600">{percent === null ? 'no OCR overlap data' : `OCR overlap ${percent}`}</span>
                {pair.candidates.length === 0
                  ? <span className="text-gray-400 italic">linked through other members</span>
                  : pair.candidates.map((c, i) => (
                    <span key={i} className="rounded bg-gray-100 px-2 py-0.5 text-xs">{`${c.source} ${c.distance.toFixed(3)}`}</span>
                  ))}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify they pass, lint, commit**

Run: `pnpm exec vitest run src/components/duplicates` then `pnpm exec eslint src/components/duplicates src/duplicates --max-warnings 0` and `pnpm exec tsc -b`.
Expected: 8 passed; lint and tsc clean.

```bash
git add -A :/
git commit -m "feat(dedup): cluster details panel"
```

---

### Task 5: Wire the panel into the list, docs, whole-root verification

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx` (replace `similarityByCluster` / `fetchedSimilarityRef` / `maybeFetchSimilarity`, lines ~40-41 and 177-190; render the toggle and panel in the cluster row, near line 357-420)
- Test: `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx` (append a `describe('MemesDuplicatesList details panel', ...)`)

**Interfaces:**
- Consumes: `useClusterSimilarity(memesApi)` → `{ entries, load, retry }`, `ClusterDetailsPanel` (Tasks 3 and 4).
- Produces: nothing new; the list page behaviour only.

- [ ] **Step 1: Write the failing list tests**

Append to `MemesDuplicatesList.test.tsx` (it already imports `act, fireEvent, render, screen, waitFor`, `MemesDuplicatesList`, `makeMockApi`, `Meme`, and defines `clusterMeme`). Look at how the existing "partial selection" tests build a mocked `iterateDuplicates` response and mirror it exactly; the tests below assume a helper `apiWithCluster(members, overrides)` that you add next to them using that same shape:

```tsx
describe('MemesDuplicatesList details panel', () => {
  async function renderCluster(overrides = {}) {
    const members = [
      { ...clusterMeme('a', 1), text: ['alpha words (0.9)'] },
      { ...clusterMeme('b', 1), text: [] },
    ]
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({ items: members, hasNext: false, facets: [] }),
      ...overrides,
    })
    render(<MemesDuplicatesList memesApi={api} />)
    await screen.findByRole('button', { name: /details/i })
    return api
  }

  it('is collapsed by default and fetches nothing until opened', async () => {
    const api = await renderCluster()
    expect(screen.queryByText('alpha words (0.9)')).not.toBeInTheDocument()
    expect(api.getClusterSimilarity).not.toHaveBeenCalled()
  })

  it('opening Details shows member OCR text and fetches once', async () => {
    const api = await renderCluster()
    const toggle = screen.getByRole('button', { name: /details/i })
    expect(toggle.className).toContain('min-h-11')
    fireEvent.click(toggle)
    expect(await screen.findByText('alpha words (0.9)')).toBeInTheDocument()
    await waitFor(() => expect(api.getClusterSimilarity).toHaveBeenCalledTimes(1))
    expect(api.getClusterSimilarity).toHaveBeenCalledWith(1)
  })

  it('closing and reopening does not fetch again', async () => {
    const api = await renderCluster()
    const toggle = screen.getByRole('button', { name: /details/i })
    fireEvent.click(toggle)
    await screen.findByText('alpha words (0.9)')
    fireEvent.click(screen.getByRole('button', { name: /details/i }))
    fireEvent.click(screen.getByRole('button', { name: /details/i }))
    await waitFor(() => expect(api.getClusterSimilarity).toHaveBeenCalledTimes(1))
  })

  it('selecting two members after opening Details still makes one request', async () => {
    const api = await renderCluster()
    fireEvent.click(screen.getByRole('button', { name: /details/i }))
    await screen.findByText('alpha words (0.9)')
    fireEvent.click(screen.getByLabelText('Select all'))
    await waitFor(() => expect(api.getClusterSimilarity).toHaveBeenCalledTimes(1))
  })

  it('shows a retry after a failed fetch and recovers', async () => {
    const getClusterSimilarity = vi.fn()
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ pairs: [], capped: false })
    await renderCluster({ getClusterSimilarity })
    fireEvent.click(screen.getByRole('button', { name: /details/i }))
    fireEvent.click(await screen.findByRole('button', { name: /retry/i }))
    await waitFor(() => expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument())
    expect(getClusterSimilarity).toHaveBeenCalledTimes(2)
  })

  it('a card badge with a null overlap shows no score and does not crash', async () => {
    const getClusterSimilarity = vi.fn().mockResolvedValue({
      pairs: [{ image_id1: 'a', image_id2: 'b', overlap: null, candidates: [] }], capped: false,
    })
    await renderCluster({ getClusterSimilarity })
    fireEvent.click(screen.getByLabelText('Select all'))
    await waitFor(() => expect(getClusterSimilarity).toHaveBeenCalled())
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run to verify they fail**

Run: `pnpm exec vitest run src/components/MemesDuplicatesList.test.tsx -t "details panel"`
Expected: FAIL (no "Details" button).

- [ ] **Step 3: Wire the hook and panel**

In `MemesDuplicatesList.tsx`:
1. Remove `similarityByCluster`, `fetchedSimilarityRef` (lines ~40-41) and `maybeFetchSimilarity` (lines ~177-190). Add `const { entries: similarityEntries, load: loadSimilarity, retry: retrySimilarity } = useClusterSimilarity(memesApi)` and `const [openDetails, setOpenDetails] = useState<Set<number>>(new Set())`.
2. In `toggleSelect` and `selectAll` replace `maybeFetchSimilarity(clusterId)` by `loadSimilarity(clusterId)` (and the dependency arrays by `[loadSimilarity]`).
3. Where `similarityMap` is read (line ~357) derive the badge map from the entry, skipping null overlaps:

```tsx
const similarityEntry = typeof clusterId === "number" ? similarityEntries.get(clusterId) : undefined
const similarityMap = similarityEntry?.status === 'ok'
  ? new Map(similarityEntry.pairs.flatMap(p => p.overlap === null ? [] : [[`${p.image_id1}:${p.image_id2}`, p.overlap] as const]))
  : undefined
```
   (Delete the null-skip added to the old `.then` in Task 3 together with that code.)
4. After the member grid and before the action buttons, inside `typeof clusterId === "number"`, add:

```tsx
<div className="mt-2">
  <button
    className="min-h-11 rounded bg-gray-100 px-4 text-sm hover:bg-gray-200"
    aria-expanded={openDetails.has(clusterId)}
    onClick={() => {
      if (!openDetails.has(clusterId)) loadSimilarity(clusterId)
      setOpenDetails(prev => { const next = new Set(prev); if (next.has(clusterId)) next.delete(clusterId); else next.add(clusterId); return next })
    }}
  >
    Details
  </button>
  {openDetails.has(clusterId) && (
    <ClusterDetailsPanel
      members={visibleMembers}
      entry={similarityEntry}
      memesApi={memesApi}
      onRetry={() => retrySimilarity(clusterId)}
    />
  )}
</div>
```
   Import `useClusterSimilarity` and `ClusterDetailsPanel`. Keep the loader inside the click handler (not an effect) so a StrictMode double mount cannot double the request.

- [ ] **Step 4: Run to verify they pass**

Run: `pnpm exec vitest run src/components/MemesDuplicatesList.test.tsx`
Expected: all pass (the new 6 and every existing test).

- [ ] **Step 5: Whole-root verification**

Run each root separately and read the counts (record them in the ledger):
- `cd /h/workspace_sandbox/memes && DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/integration -q`
- `cd /h/workspace_sandbox/memes/Backend && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest -q`
- `cd /h/workspace_sandbox/memes && H:/workspace_sandbox/memes/.venv311/Scripts/python.exe -m pytest tests/docs -q`, then the same for `batch/tests` and `tests/rules`.
- `cd /h/workspace_sandbox/memes/Frontend/memes-frontend && pnpm exec tsc -b && pnpm exec eslint src/ --max-warnings 0 && pnpm exec vitest run`
- `cd /h/workspace_sandbox/memes/Frontend && bash generate-types.sh` then `git diff --stat Frontend/memes-frontend/src/types/generated/` must show no further change.
Expected: all green. Any failure is reported by name.

- [ ] **Step 6: Docs and status, commit**

The spec status is already `planned` with its `Plan:` link (set when this plan was written); it becomes `done` after the merge.

```bash
git add -A :/
git commit -m "feat(dedup): details panel on the duplicates page"
```
