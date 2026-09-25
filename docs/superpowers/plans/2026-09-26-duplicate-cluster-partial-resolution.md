# Duplicate-Cluster Partial Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a reviewer on the web `/duplicates` page select a subset of a duplicate cluster's members (not necessarily all of them) and apply either "not duplicates" (writes `duplicate_decisions` for that subset only) or "duplicates — keep best" (flags every selected member except a chosen keeper) — replacing today's all-or-nothing `dismiss_cluster`.

**Architecture:** Additive schema column (`ImageExtras.flagged_reason`) distinguishes this feature's bulk flag action from the pre-existing, unrelated, general-purpose per-card "Flagged" checkbox. `dismiss_cluster` gains an optional `member_ids` param (omitted = today's exact full-cluster behavior). A new lazily-fetched, presentation-only endpoint surfaces a lemma-overlap-coefficient similarity hint per cluster, reusing the metric the OCR-text duplicate-matching corroboration gate already validated — kept out of the main paginated/virtualized `/duplicates` response to avoid an O(members²) cost on every row.

**Tech Stack:** FastAPI + SQLAlchemy async ORM + Alembic (Backend), React + TypeScript + `react-virtuoso` (Frontend), pytest (`tests/integration/` for real-DB repository tests, `Backend/tests/` for mocked-session service/API tests), `vitest` + Testing Library (Frontend).

**Spec:** `docs/superpowers/specs/2026-09-26-duplicate-cluster-partial-resolution.md`

## Global Constraints

- The existing manual per-card "Flagged" checkbox (`MemeCard.tsx`'s existing `isFlagged` state → `memesApi.markImageIsFlagged(meme.id)`/`unmarkImageIsFlagged(meme.id)`) must keep calling with exactly the arguments it does today — no reason. Its existing tests (`MemeCard.test.tsx:61,70`) must keep passing unmodified.
- `dismiss_cluster(cluster_id)` with no second argument, and `mark_flagged(image_id)` with no second argument, must keep behaving byte-for-byte identically to today (backward compatibility for any caller that doesn't know about the new param).
- No change to `clusterize.py`, `rebuild_duplicates.py`, `ingest_find_duplicates.py`, or any automatic-detection logic anywhere in this plan.
- The similarity-coefficient computation is presentation-only — it is never written to any table and never gates a decision.
- Several **existing** tests assert the exact old call signatures (`mock_image_service.mark_flagged.assert_called_once_with("123")`, `mock_image_service.dismiss_cluster.assert_called_once_with(141)`, `expect(api.dismissDuplicateCluster).toHaveBeenCalledWith(1)`, and the `service` fixture in `Backend/tests/test_image_service.py` constructs `ImageService(mock_repo, mock_decision_repo)` with exactly two args). Each task below names exactly which existing assertions/fixtures must be updated and how — this is expected, in-scope test maintenance for this plan, not a regression to avoid.

---

### Task 1: Schema + `ImageRepository.set_flagged` reason support

**Files:**
- Create: `Storage/alembic/versions/<generated>_add_flagged_reason_to_image_extras.py`
- Modify: `Storage/models.py:426-434` (`ImageExtras`)
- Modify: `Backend/app/repositories/image_repository.py:580-589` (`set_flagged`)
- Test: `tests/integration/test_backend_image_repository.py`

**Interfaces:**
- Produces: `ImageExtras.flagged_reason` column (nullable `String(50)`). `ImageRepository.set_flagged(self, image_id, is_flagged, reason: str | None = None)` — Task 3 calls this with a reason for the new bulk action, and with no reason (unchanged call shape) for the existing manual-checkbox path.

- [ ] **Step 1: Edit the ORM model**

In `Storage/models.py`, add the new column to `ImageExtras`:

```python
class ImageExtras(Base):
    __tablename__ = "image_extras"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True, index=True)

    flagged = Column(Boolean)
    flagged_reason = Column(String(50), nullable=True)
    remarks = Column(Text)

    image = relationship("Image", back_populates="image_extras")
```

- [ ] **Step 2: Generate the migration**

Run (from `Storage/`, with `DATABASE_URL` set — per CLAUDE.md's Configuration section, this is required even though `--env`/`load_env()` isn't used here):

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" ../.venv311/Scripts/python.exe -m alembic revision --autogenerate -m "add flagged_reason to image_extras"
```

Expected: a new file under `Storage/alembic/versions/` whose `upgrade()` contains exactly one
`op.add_column('image_extras', sa.Column('flagged_reason', sa.String(length=50), nullable=True))`
and whose `downgrade()` contains exactly one `op.drop_column('image_extras', 'flagged_reason')` —
open the generated file and confirm it contains nothing else (autogenerate sometimes picks up
unrelated drift; if it does, trim the migration down to just this one column change and note the
drift in your report instead of committing unrelated changes).

- [ ] **Step 3: Apply the migration to the test database**

```bash
cd Storage
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" ../.venv311/Scripts/python.exe -m alembic upgrade head
```

Expected: succeeds with no errors, ends at the new revision.

- [ ] **Step 4: Write the failing repository tests**

Append to `tests/integration/test_backend_image_repository.py` (check its existing imports first —
it almost certainly already imports `ImageRepository`, `Image`, `ImageExtras`, and a `db_session`
fixture; add only what's missing):

```python
async def test_set_flagged_persists_reason(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_flagged(image.id, True, reason="duplicate_review")
    await db_session.commit()

    result = await db_session.execute(select(ImageExtras).where(ImageExtras.image_id == image.id))
    extras = result.scalar_one()
    assert extras.flagged is True
    assert extras.flagged_reason == "duplicate_review"


async def test_set_flagged_without_reason_leaves_it_null(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_flagged(image.id, True)
    await db_session.commit()

    result = await db_session.execute(select(ImageExtras).where(ImageExtras.image_id == image.id))
    extras = result.scalar_one()
    assert extras.flagged is True
    assert extras.flagged_reason is None


async def test_set_flagged_false_clears_reason(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    await repo.set_flagged(image.id, True, reason="duplicate_review")
    await db_session.commit()
    await repo.set_flagged(image.id, False)
    await db_session.commit()

    result = await db_session.execute(select(ImageExtras).where(ImageExtras.image_id == image.id))
    extras = result.scalar_one()
    assert extras.flagged is False
    assert extras.flagged_reason is None
```

(If `select` and `uuid` aren't already imported at the top of the file, add
`from sqlalchemy import select` and `import uuid`.)

- [ ] **Step 5: Run tests to verify they fail**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v -k "test_set_flagged"
```

Expected: all three FAIL with `TypeError: set_flagged() got an unexpected keyword argument 'reason'` (the first two) and the third fails at its second `set_flagged` call for the same reason — `set_flagged` doesn't accept `reason` yet.

- [ ] **Step 6: Implement**

In `Backend/app/repositories/image_repository.py`:

```python
async def set_flagged(self, image_id, is_flagged, reason: str | None = None):
    stmt = (
        insert(ImageExtras)
        .values(image_id=image_id, flagged=is_flagged, flagged_reason=reason)
        .on_conflict_do_update(
            index_elements=["image_id"],
            set_={"flagged": is_flagged, "flagged_reason": reason},
        )
    )
    await self.session.execute(stmt)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v
```

Expected: all tests in the file PASS, including the pre-existing ones (run the whole file, not
just the three new tests).

- [ ] **Step 8: Commit**

```bash
git add Storage/models.py Storage/alembic/versions/ Backend/app/repositories/image_repository.py tests/integration/test_backend_image_repository.py
git commit -m "feat: add flagged_reason column and thread it through set_flagged"
```

---

### Task 2: `OCRLemmasRepository.get_pairwise_overlap_coefficients`

**Files:**
- Modify: `repository/ocr_lemmas.py`
- Test: `tests/integration/test_ocr_lemmas_repository.py`

**Interfaces:**
- Produces: `OCRLemmasRepository.get_pairwise_overlap_coefficients(image_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, uuid.UUID], float]`. Task 3 consumes this exact signature and return shape.

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_ocr_lemmas_repository.py` (check its existing imports first — it
almost certainly already has `OCRLemmasRepository`, `OCRLemma`, `Image`, a `db_session` fixture,
and `uuid`; add only what's missing):

```python
async def test_get_pairwise_overlap_coefficients_computes_expected_ratio(db_session):
    a, b = Image(filename=f"{uuid.uuid4()}.jpg"), Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add_all([
        OCRLemma(image_id=a.id, lemma="cat"), OCRLemma(image_id=a.id, lemma="dog"),
        OCRLemma(image_id=a.id, lemma="bird"), OCRLemma(image_id=a.id, lemma="fish"),
        OCRLemma(image_id=b.id, lemma="cat"), OCRLemma(image_id=b.id, lemma="dog"),
    ])
    await db_session.flush()

    repo = OCRLemmasRepository(db_session)
    result = await repo.get_pairwise_overlap_coefficients([a.id, b.id])

    key = (min(a.id, b.id), max(a.id, b.id))
    assert result[key] == pytest.approx(1.0)  # 2 shared / min(4, 2) = 1.0


async def test_get_pairwise_overlap_coefficients_excludes_pair_with_no_lemmas(db_session):
    a, b = Image(filename=f"{uuid.uuid4()}.jpg"), Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add_all([a, b])
    await db_session.flush()
    db_session.add(OCRLemma(image_id=a.id, lemma="cat"))
    await db_session.flush()

    repo = OCRLemmasRepository(db_session)
    result = await repo.get_pairwise_overlap_coefficients([a.id, b.id])

    assert result == {}


async def test_get_pairwise_overlap_coefficients_single_or_empty_input_returns_empty(db_session):
    repo = OCRLemmasRepository(db_session)

    assert await repo.get_pairwise_overlap_coefficients([]) == {}
    assert await repo.get_pairwise_overlap_coefficients([uuid.uuid4()]) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v -k "overlap_coefficients"
```

Expected: all three FAIL with `AttributeError: 'OCRLemmasRepository' object has no attribute 'get_pairwise_overlap_coefficients'`.

- [ ] **Step 3: Implement**

In `repository/ocr_lemmas.py`, add to `OCRLemmasRepository` (add `from Storage.models import OCRLemma`
and `import uuid` to the file's imports if not already present):

```python
    async def get_pairwise_overlap_coefficients(self, image_ids: list[uuid.UUID]) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
        """Overlap coefficient (shared_lemmas / min(lemma_count_1, lemma_count_2)) for every pair
        within image_ids -- same formula batch/rebuild_duplicates.py's corroboration gate uses
        (_OCR_LEMMA_OVERLAP_CHECK), computed directly rather than reused verbatim since that SQL
        fragment is correlated against a specific LATERAL-join probe/nn pair, not a free list of
        ids. Presentation-only: never written anywhere. Pairs where either side has zero
        ocr_lemmas rows are simply absent from the result (nothing to compare), not scored 0.0 --
        the caller renders "no OCR text" for those, not a false "definitely different" signal."""
        if len(image_ids) < 2:
            return {}
        rows = await self.session.execute(
            select(OCRLemma.image_id, OCRLemma.lemma).where(OCRLemma.image_id.in_(image_ids))
        )
        lemmas_by_image: dict[uuid.UUID, set[str]] = {}
        for image_id, lemma in rows:
            lemmas_by_image.setdefault(image_id, set()).add(lemma)

        result: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
        ids_with_lemmas = [i for i in image_ids if i in lemmas_by_image]
        for i, a in enumerate(ids_with_lemmas):
            for b in ids_with_lemmas[i + 1:]:
                shared = len(lemmas_by_image[a] & lemmas_by_image[b])
                denom = min(len(lemmas_by_image[a]), len(lemmas_by_image[b]))
                result[(min(a, b), max(a, b))] = shared / denom if denom else 0.0
        return result
```

(`select` is almost certainly already imported in this file for its existing methods — confirm
before adding a duplicate import.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_ocr_lemmas_repository.py -v
```

Expected: all tests in the file PASS.

- [ ] **Step 5: Run the full integration root**

Per CLAUDE.md's "Running the right test scope" gotcha, `repository/ocr_lemmas.py` is shared code
(consumed by batch scripts and the Backend) — run the full root as a sanity check:

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add repository/ocr_lemmas.py tests/integration/test_ocr_lemmas_repository.py
git commit -m "feat: add pairwise OCR-lemma overlap coefficient computation"
```

---

### Task 3: Service layer

**Files:**
- Modify: `Backend/app/services/image_service.py`
- Test: `Backend/tests/test_image_service.py`

**Interfaces:**
- Consumes: `ImageRepository.set_flagged(image_id, is_flagged, reason=None)` (Task 1),
  `OCRLemmasRepository.get_pairwise_overlap_coefficients(image_ids)` (Task 2).
- Produces: `ImageService.__init__(self, repo, decision_repo, ocr_lemmas_repo)` (now **three**
  required args — Task 4's DI wiring must pass all three). `dismiss_cluster(cluster_id,
  member_ids=None)`, `mark_flagged(image_id, reason=None)`, and new
  `get_cluster_overlap_coefficients(cluster_id)`. Task 4 consumes all three exact names.

- [ ] **Step 1: Update the existing test fixtures (they construct `ImageService` directly)**

In `Backend/tests/test_image_service.py`, the `service` fixture currently constructs
`ImageService(mock_repo, mock_decision_repo)` with two args — this now needs a third. Add a new
fixture and update `service` to use it:

```python
@pytest.fixture
def mock_ocr_lemmas_repo():
    return AsyncMock()


@pytest.fixture
def service(mock_repo, mock_decision_repo, mock_ocr_lemmas_repo):
    return ImageService(mock_repo, mock_decision_repo, mock_ocr_lemmas_repo)
```

- [ ] **Step 2: Update the two existing assertions that will change shape**

`TestDismissCluster::test_dismiss_generates_all_pairs_for_current_members` currently asserts
`mock_repo.get_cluster_member_ids.assert_awaited_once_with(141)` — this call site is unchanged (it
still just fetches all members when `member_ids` isn't given), so this specific assertion needs no
change. No existing assertion in this file calls `dismiss_cluster` or `mark_flagged` with a second
positional argument to compare against — the two-arg-vs-one-arg concern is entirely at the router
layer (`Backend/tests/test_images_endpoints.py`, not this file) since this file calls
`service.dismiss_cluster(141)` directly, which the new default-`None` second parameter keeps
working for unchanged.

- [ ] **Step 3: Write the new failing tests**

Append to `Backend/tests/test_image_service.py`:

```python
class TestDismissClusterSubset:
    async def test_dismiss_subset_generates_only_subset_pairs(self, service, mock_repo, mock_decision_repo):
        a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_repo.get_cluster_member_ids.return_value = [a, b, c]

        pairs = await service.dismiss_cluster(141, member_ids=[a, b])

        assert sorted(pairs) == sorted([(a, b)])
        mock_decision_repo.record_decisions_bulk.assert_awaited_once_with([(a, b)])

    async def test_dismiss_subset_rejects_id_not_in_cluster(self, service, mock_repo):
        a, b = uuid.uuid4(), uuid.uuid4()
        outsider = uuid.uuid4()
        mock_repo.get_cluster_member_ids.return_value = [a, b]

        with pytest.raises(HTTPException) as exc_info:
            await service.dismiss_cluster(141, member_ids=[a, outsider])

        assert exc_info.value.status_code == 400

    async def test_dismiss_subset_of_one_is_rejected(self, service, mock_repo):
        a, b = uuid.uuid4(), uuid.uuid4()
        mock_repo.get_cluster_member_ids.return_value = [a, b]

        with pytest.raises(HTTPException) as exc_info:
            await service.dismiss_cluster(141, member_ids=[a])

        assert exc_info.value.status_code == 400


class TestMarkFlaggedReason:
    async def test_mark_flagged_passes_reason_through(self, service, mock_repo):
        image_id = uuid.uuid4()

        await service.mark_flagged(image_id, reason="duplicate_review")

        mock_repo.set_flagged.assert_awaited_once_with(image_id, True, "duplicate_review")

    async def test_mark_flagged_without_reason_passes_none(self, service, mock_repo):
        image_id = uuid.uuid4()

        await service.mark_flagged(image_id)

        mock_repo.set_flagged.assert_awaited_once_with(image_id, True, None)


class TestClusterOverlapCoefficients:
    async def test_delegates_member_ids_to_ocr_lemmas_repo(self, service, mock_repo, mock_ocr_lemmas_repo):
        a, b = uuid.uuid4(), uuid.uuid4()
        mock_repo.get_cluster_member_ids.return_value = [a, b]
        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.return_value = {(a, b): 0.5}

        result = await service.get_cluster_overlap_coefficients(141)

        mock_ocr_lemmas_repo.get_pairwise_overlap_coefficients.assert_awaited_once_with([a, b])
        assert result == {(a, b): 0.5}
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
cd Backend && pytest tests/test_image_service.py -v
```

Expected: the pre-existing tests now FAIL at fixture construction time
(`TypeError: ImageService.__init__() missing 1 required positional argument: 'ocr_lemmas_repo'`)
until Step 5 lands — this is expected mid-step breakage, not a defect; the new tests fail for the
same reason plus `AttributeError`s for the not-yet-added methods.

- [ ] **Step 5: Implement**

In `Backend/app/services/image_service.py`:

```python
    def __init__(self, repo: ImageRepository, decision_repo: DuplicateDecisionsRepository, ocr_lemmas_repo: OCRLemmasRepository):
        self.repo = repo
        self.decision_repo = decision_repo
        self.ocr_lemmas_repo = ocr_lemmas_repo
```

(Match whatever the existing `__init__` body actually assigns beyond `self.repo`/`self.decision_repo`
— add the one new line, don't remove anything already there. Add
`from repository.ocr_lemmas import OCRLemmasRepository` to the file's imports.)

```python
    async def dismiss_cluster(self, cluster_id: int, member_ids: list[uuid.UUID] | None = None) -> list[tuple[uuid.UUID, uuid.UUID]]:
        all_member_ids = await self.repo.get_cluster_member_ids(cluster_id)
        if not all_member_ids:
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id} not found")

        if member_ids is None:
            target_ids = all_member_ids
        else:
            invalid = set(member_ids) - set(all_member_ids)
            if invalid:
                raise HTTPException(status_code=400, detail=f"Not members of cluster {cluster_id}: {invalid}")
            if len(member_ids) < 2:
                raise HTTPException(status_code=400, detail="Need at least 2 members to dismiss a subset")
            target_ids = member_ids

        pairs = [
            (target_ids[i], target_ids[j])
            for i in range(len(target_ids))
            for j in range(i + 1, len(target_ids))
        ]
        await self.decision_repo.record_decisions_bulk(pairs)
        return pairs


    async def mark_flagged(self, image_id, reason: str | None = None):
        await self.repo.set_flagged(image_id, True, reason)


    async def get_cluster_overlap_coefficients(self, cluster_id: int) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
        member_ids = await self.repo.get_cluster_member_ids(cluster_id)
        return await self.ocr_lemmas_repo.get_pairwise_overlap_coefficients(member_ids)
```

Replace the existing `dismiss_cluster` and `mark_flagged` method bodies with these (same method
names, same file location — this is an edit, not an addition, for those two); `get_cluster_overlap_coefficients`
is new. Leave `unmark_flagged` exactly as it is today (unchanged, no reason param).

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd Backend && pytest tests/test_image_service.py -v
```

Expected: all tests in the file PASS, including every pre-existing test (run the whole file).

- [ ] **Step 7: Run the full Backend suite**

```bash
cd Backend && pytest -q
```

Expected: PASS except `Backend/tests/test_images_endpoints.py`'s tests that construct/call through
`ImageService` with the old assumptions — those are Task 4's responsibility to fix; if any other,
unrelated test file breaks, stop and report it, don't fix it yourself (out of this task's scope).

- [ ] **Step 8: Commit**

```bash
git add Backend/app/services/image_service.py Backend/tests/test_image_service.py
git commit -m "feat: service-layer support for subset dismiss, flag reasons, and cluster similarity"
```

---

### Task 4: API layer

**Files:**
- Modify: `Backend/app/api/images.py`
- Modify: `backend_api.md`
- Create: `shared/schemas/duplicatedismissrequest.schema.json`, `shared/schemas/clustersimilaritypair.schema.json`, `shared/schemas/clustersimilarityresponse.schema.json`
- Modify: `shared/schemas/all.schema.json` (if it explicitly lists every schema — check its
  existing pattern first; some schemas are only ever referenced via `$ref` from a response schema
  and never listed there directly)
- Test: `Backend/tests/test_images_endpoints.py`

**Interfaces:**
- Consumes: `ImageService.dismiss_cluster(cluster_id, member_ids=None)`,
  `ImageService.mark_flagged(image_id, reason=None)`,
  `ImageService.get_cluster_overlap_coefficients(cluster_id)` (Task 3).
- Produces: the three live HTTP endpoints below. Task 6 (frontend) consumes their exact request/response shapes.

- [ ] **Step 1: Write the failing tests**

In `Backend/tests/test_images_endpoints.py`:

**The `mock_image_service` fixture (`test_images_endpoints.py:38-42`) is a bare `AsyncMock()`, no
`spec=`** — new methods and extra call arguments just work automatically, no fixture change needed
for this task.

Update the three existing `mark_flagged` assertions to match the new two-argument call shape
(the router now always passes `reason` through, defaulting to `None`):

```python
    def test_mark_flagged_success(self, client, mock_image_service):
        """Test successfully marking an image as flagged."""
        # Arrange
        mock_image_service.mark_flagged.return_value = None

        # Act
        response = client.put("/api/images/meme/123/mark_flagged")

        # Assert
        assert response.status_code == 200
        mock_image_service.mark_flagged.assert_called_once_with("123", None)
```

(Same one-line change — add `, None` — to `test_mark_flagged_with_uuid_format`'s
`assert_called_once_with(uuid_id)` → `assert_called_once_with(uuid_id, None)`.
`test_mark_flagged_multiple_times` only checks `call_count`, no change needed.
`test_mark_flagged_service_error` doesn't assert call args, no change needed.)

Add a new test for the reason query param:

```python
    def test_mark_flagged_with_reason(self, client, mock_image_service):
        mock_image_service.mark_flagged.return_value = None

        response = client.put("/api/images/meme/123/mark_flagged?reason=duplicate_review")

        assert response.status_code == 200
        mock_image_service.mark_flagged.assert_called_once_with("123", "duplicate_review")
```

Update the existing dismiss test's assertion the same way:

```python
    def test_dismiss_success(self, client, mock_image_service):
        mock_image_service.dismiss_cluster.return_value = [
            (uuid.UUID("11111111-1111-1111-1111-111111111111"),
             uuid.UUID("22222222-2222-2222-2222-222222222222")),
        ]

        response = client.post("/api/images/duplicates/clusters/141/dismiss")

        assert response.status_code == 200
        data = response.json()
        assert data["pairs"] == [{
            "image_id1": "11111111-1111-1111-1111-111111111111",
            "image_id2": "22222222-2222-2222-2222-222222222222",
        }]
        mock_image_service.dismiss_cluster.assert_called_once_with(141, None)
```

Add new tests for the subset body and the similarity endpoint:

```python
    def test_dismiss_with_member_ids(self, client, mock_image_service):
        mock_image_service.dismiss_cluster.return_value = [
            (uuid.UUID("11111111-1111-1111-1111-111111111111"),
             uuid.UUID("22222222-2222-2222-2222-222222222222")),
        ]

        response = client.post(
            "/api/images/duplicates/clusters/141/dismiss",
            json={"member_ids": [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ]},
        )

        assert response.status_code == 200
        mock_image_service.dismiss_cluster.assert_called_once_with(
            141,
            [uuid.UUID("11111111-1111-1111-1111-111111111111"),
             uuid.UUID("22222222-2222-2222-2222-222222222222")],
        )


class TestClusterSimilarity:
    """Tests for GET /api/images/duplicates/clusters/{cluster_id}/similarity."""

    def test_similarity_success(self, client, mock_image_service):
        a = uuid.UUID("11111111-1111-1111-1111-111111111111")
        b = uuid.UUID("22222222-2222-2222-2222-222222222222")
        mock_image_service.get_cluster_overlap_coefficients.return_value = {(a, b): 0.75}

        response = client.get("/api/images/duplicates/clusters/141/similarity")

        assert response.status_code == 200
        data = response.json()
        assert data["pairs"] == [{
            "image_id1": str(a), "image_id2": str(b), "overlap": 0.75,
        }]
```

Add both new test classes/methods at the end of the file, or immediately after `TestDismissDuplicateCluster` for `test_dismiss_with_member_ids` and after `TestUndoDismissDuplicates` for `TestClusterSimilarity` — match whatever ordering the rest of the file already follows for endpoint declaration order.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd Backend && pytest tests/test_images_endpoints.py -v -k "mark_flagged or dismiss or similarity"
```

Expected: the three updated assertions FAIL (old one-arg form no longer matches what Task 3's
`ImageService` methods expect callers to pass — actually, at this point in the plan, the router
itself hasn't changed yet, so these FAIL because the router still calls the old one-arg form while
the test now expects two args). The new tests FAIL — `test_mark_flagged_with_reason` because the
endpoint doesn't accept `?reason=`; `test_dismiss_with_member_ids` because the endpoint doesn't
accept a body; `test_similarity_success` with a 404 (route doesn't exist yet).

- [ ] **Step 3: Implement the endpoint changes**

In `Backend/app/api/images.py`:

```python
@router.put("/meme/{image_id}/mark_flagged")
async def mark_flagged(
    image_id: str,
    response: Response,
    reason: str | None = Query(None, max_length=50),
    service: ImageService = Depends(get_image_service),
):
    await service.mark_flagged(image_id, reason)
```

(`unmark_flagged` is unchanged — do not touch it.)

```python
class DuplicateDismissRequestModel(BaseModel):
    member_ids: list[str] | None = None


@router.post("/duplicates/clusters/{cluster_id}/dismiss", response_model=DuplicateDismissResponseModel)
async def dismiss_duplicate_cluster(
    cluster_id: int,
    response: Response,
    body: DuplicateDismissRequestModel = DuplicateDismissRequestModel(),
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    member_ids = [uuid.UUID(i) for i in body.member_ids] if body.member_ids else None
    pairs = await service.dismiss_cluster(cluster_id, member_ids)
    return DuplicateDismissResponseModel(
        pairs=[DuplicatePairModel(image_id1=str(a), image_id2=str(b)) for a, b in pairs]
    )
```

(Add `DuplicateDismissRequestModel` near the other `Duplicate*Model` classes already declared in
this file; replace the existing `dismiss_duplicate_cluster` function with this version — same
route, same name.)

```python
class ClusterSimilarityPairModel(BaseModel):
    image_id1: str
    image_id2: str
    overlap: float


class ClusterSimilarityResponseModel(BaseModel):
    pairs: list[ClusterSimilarityPairModel]


@router.get("/duplicates/clusters/{cluster_id}/similarity", response_model=ClusterSimilarityResponseModel)
async def get_cluster_similarity(
    cluster_id: int,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    pairs = await service.get_cluster_overlap_coefficients(cluster_id)
    return ClusterSimilarityResponseModel(
        pairs=[
            ClusterSimilarityPairModel(image_id1=str(a), image_id2=str(b), overlap=coeff)
            for (a, b), coeff in pairs.items()
        ]
    )
```

Add this new router function anywhere alongside the other `/duplicates/clusters/...` endpoints —
after `dismiss_duplicate_cluster` is a natural spot.

Update `get_image_service` (the DI function, `Backend/app/api/images.py` — search for `def
get_image_service`) to construct `ImageService` with the third dependency:

```python
async def get_image_service(
    db: AsyncSessionLocal = Depends(get_async_db)
) -> AsyncGenerator[ImageService, None]:
    repository = ImageRepository(db)
    decision_repository = DuplicateDecisionsRepository(db)
    ocr_lemmas_repository = OCRLemmasRepository(db)
    service = ImageService(repository, decision_repository, ocr_lemmas_repository)
    try:
        yield service
    finally:
        # Optionally do cleanup if needed
        pass
```

Add `from repository.ocr_lemmas import OCRLemmasRepository` to this file's imports if not already
present (it will be, if Task 3 needed it elsewhere in the Backend — check first, don't
double-import).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd Backend && pytest tests/test_images_endpoints.py -v
```

Expected: all tests in the file PASS, including every pre-existing test.

- [ ] **Step 5: Run the full Backend suite**

```bash
cd Backend && pytest -q
```

Expected: all PASS.

- [ ] **Step 6: Add the three new schema files**

`shared/schemas/duplicatedismissrequest.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "duplicatedismissrequest.schema.json",
  "title": "DuplicateDismissRequest",
  "type": "object",
  "properties": {
    "member_ids": {
      "type": ["array", "null"],
      "items": { "type": "string" },
      "description": "Subset of the cluster's member ids to dismiss as not-duplicates. Omitted or null dismisses the whole cluster (today's existing behavior)."
    }
  }
}
```

`shared/schemas/clustersimilaritypair.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "clustersimilaritypair.schema.json",
  "title": "ClusterSimilarityPair",
  "type": "object",
  "properties": {
    "image_id1": { "type": "string" },
    "image_id2": { "type": "string" },
    "overlap": { "type": "number", "description": "OCR-lemma overlap coefficient, 0.0-1.0. Presentation-only, never written anywhere." }
  },
  "required": ["image_id1", "image_id2", "overlap"]
}
```

`shared/schemas/clustersimilarityresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "clustersimilarityresponse.schema.json",
  "title": "ClusterSimilarityResponse",
  "type": "object",
  "properties": {
    "pairs": { "type": "array", "items": { "$ref": "./clustersimilaritypair.schema.json" } }
  },
  "required": ["pairs"]
}
```

Check `shared/schemas/all.schema.json`'s existing pattern (open it and look for how
`duplicatepair.schema.json`/`duplicatedismissresponse.schema.json` are referenced there — likely
each top-level response schema gets a `$ref` entry, while a schema only ever used as a nested
`$ref` target like `DuplicatePair` might or might not need its own top-level entry depending on
`json2ts`'s `--unreachableDefinitions` flag, which `generate-types.sh` already passes). Add
entries for the three new files following whatever pattern the existing `Duplicate*` entries use.

- [ ] **Step 7: Regenerate all three type trees**

```bash
cd Frontend && bash generate-types.sh
python AndroidClient/scripts/generate_dtos.py
```

```bash
cd Backend && datamodel-codegen --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
```

Expected: `Frontend/memes-frontend/src/types/generated/all.d.ts` gains
`DuplicateDismissRequest`/`ClusterSimilarityPair`/`ClusterSimilarityResponse` interfaces;
`AndroidClient/.../Models.kt` and `Backend/app/types/generated/` gain the matching classes. Per
this codebase's own documented gotcha, the hand-written `DuplicateDismissRequestModel`/
`ClusterSimilarityPairModel`/`ClusterSimilarityResponseModel` classes already added to
`Backend/app/api/images.py` in Step 3 are the ones actually used by `response_model=`/request
bodies — this generation step keeps the *generated* Python tree in sync for documentation/tooling
purposes, it does not by itself change live endpoint behavior (already true and already handled,
since Step 3's hand-written classes already match).

- [ ] **Step 8: Update `backend_api.md`**

Add entries for the two new/changed endpoints, following the file's existing per-endpoint format
(URL, Method, Response, Example — match the style of the existing `dismiss_duplicate_cluster`
entry exactly):

- `POST /api/images/duplicates/clusters/{cluster_id}/dismiss` — update its existing doc entry to
  note the optional `{"member_ids": [...]}` request body and its "omit for full-cluster dismiss,
  today's existing behavior" semantics.
- `PUT /api/images/meme/{image_id}/mark_flagged` — update its existing doc entry to note the
  optional `?reason=` query param.
- `GET /api/images/duplicates/clusters/{cluster_id}/similarity` — new entry, following the format
  of the nearest existing `GET` endpoint doc in the file, with an example response shape matching
  `ClusterSimilarityResponseModel`.

- [ ] **Step 9: Verify no diff drift**

```bash
git status --short
git diff Frontend/memes-frontend/src/types/generated/
```

Confirm only the expected files changed, per CLAUDE.md's CI gate on a clean generated-types diff.

- [ ] **Step 10: Commit**

```bash
git add Backend/app/api/images.py Backend/tests/test_images_endpoints.py backend_api.md shared/schemas/ Frontend/memes-frontend/src/types/generated/ AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt Backend/app/types/generated/
git commit -m "feat: API endpoints for subset dismiss, flag reasons, and cluster similarity"
```

---

### Task 5: Frontend API layer + `MemeCard.tsx`

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts`
- Modify: `Frontend/memes-frontend/src/components/MemeCard.tsx`
- Test: `Frontend/memes-frontend/src/components/MemeCard.test.tsx`

**Interfaces:**
- Produces: `MemesApi.dismissDuplicateCluster(clusterId, memberIds?: string[])`,
  `MemesApi.markImageIsFlagged(id, reason?: string)`,
  `MemesApi.getClusterSimilarity(clusterId): Promise<ClusterSimilarityResponse>` (new method).
  `MemeCard` gains `selectable?`, `selected?`, `onToggleSelect?`, `isKeeper?`, `onSetKeeper?`,
  `similarity?` props — Task 6 consumes all of these exact prop names.

- [ ] **Step 1: Update the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, change the two existing method signatures and
add one new method (keep every other existing method in the interface untouched):

```typescript
  dismissDuplicateCluster(clusterId: number, memberIds?: string[]): Promise<DuplicateDismissResponse>;
```

```typescript
  markImageIsFlagged(id: string, reason?: string): Promise<void>;
```

```typescript
  getClusterSimilarity(clusterId: number): Promise<ClusterSimilarityResponse>;
```

Add `import type { ClusterSimilarityResponse } from "../types/generated/all"` (or wherever this
file's existing generated-type imports come from) if `ClusterSimilarityResponse` isn't already
imported — it will exist post-Task-4's type regeneration.

- [ ] **Step 2: Update `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`:

```typescript
  async dismissDuplicateCluster(clusterId: number, memberIds?: string[]): Promise<DuplicateDismissResponse> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/clusters/${clusterId}/dismiss`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ member_ids: memberIds ?? null }),
    })
    if (!res.ok) throw new Error(`Failed to dismiss cluster ${clusterId}: ${res.status}`)
    return res.json()
  }
```

```typescript
  async markImageIsFlagged(id: string, reason?: string): Promise<void> {
    const params = reason ? `?reason=${encodeURIComponent(reason)}` : ""
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/mark_flagged${params}`, {
      method: "PUT"
    })

    if (!res.ok) throw new Error("Failed mark as flagged")
  }
```

```typescript
  async getClusterSimilarity(clusterId: number): Promise<ClusterSimilarityResponse> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/clusters/${clusterId}/similarity`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch similarity for cluster ${clusterId}: ${res.status}`)
    return res.json()
  }
```

Replace the two existing method bodies in place (same method names, same file); add the third as a
new method near `dismissDuplicateCluster`.

- [ ] **Step 3: Update `mockApi.ts`**

In `Frontend/memes-frontend/src/test/mockApi.ts`, the existing lines:

```typescript
    markImageIsFlagged: vi.fn().mockResolvedValue(undefined),
```
```typescript
    dismissDuplicateCluster: vi.fn().mockResolvedValue({ pairs: [] }),
```

need no signature change (mocks accept any args already) — but add the new method to the mock's
returned object:

```typescript
    getClusterSimilarity: vi.fn().mockResolvedValue({ pairs: [] }),
```

Add this line near `dismissDuplicateCluster`'s existing line.

- [ ] **Step 4: Write the failing `MemeCard` tests**

Append to `Frontend/memes-frontend/src/components/MemeCard.test.tsx` (check its existing imports
and a `baseMeme`-style fixture helper first, matching whatever pattern its current tests already
use — the two existing flagged-checkbox tests at lines 61/70 are the closest precedent):

```typescript
describe('MemeCard selection (duplicates review)', () => {
  it('renders a selection checkbox only when selectable, and calls onToggleSelect', () => {
    const api = makeMockApi()
    const onToggleSelect = vi.fn()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} selectable selected={false} onToggleSelect={onToggleSelect} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Select' }))

    expect(onToggleSelect).toHaveBeenCalledTimes(1)
  })

  it('does not render a selection checkbox when not selectable', () => {
    const api = makeMockApi()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} />)

    expect(screen.queryByRole('checkbox', { name: 'Select' })).not.toBeInTheDocument()
  })

  it('renders a keeper control only while selected, and calls onSetKeeper', () => {
    const api = makeMockApi()
    const onSetKeeper = vi.fn()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} selectable selected onToggleSelect={vi.fn()} isKeeper={false} onSetKeeper={onSetKeeper} />)

    fireEvent.click(screen.getByRole('radio', { name: 'Keeper' }))

    expect(onSetKeeper).toHaveBeenCalledTimes(1)
  })

  it('renders a similarity badge when a similarity score is provided', () => {
    const api = makeMockApi()
    const meme = { id: 'a', imageUrl: '/a.jpg', text: [], tags: [] }
    render(<MemeCard meme={meme} memesApi={api} similarity={0.82} />)

    expect(screen.getByText('82%')).toBeInTheDocument()
  })
})
```

- [ ] **Step 5: Run tests to verify they fail**

```bash
cd Frontend/memes-frontend && npx vitest run src/components/MemeCard.test.tsx
```

Expected: the four new tests FAIL (no selection checkbox, no keeper control, no similarity badge
exist yet); every pre-existing test in the file still PASSES (confirms the new props are additive).

- [ ] **Step 6: Implement**

In `Frontend/memes-frontend/src/components/MemeCard.tsx`:

```tsx
type Props = {
  meme: Meme
  memesApi: MemesApi
  onClick?: () => void
  variant?: "square" | "full"  // 👈
  selectable?: boolean
  selected?: boolean
  onToggleSelect?: () => void
  isKeeper?: boolean
  onSetKeeper?: () => void
  similarity?: number
}

export default function MemeCard({ meme, memesApi, onClick, variant = "square", selectable = false, selected = false, onToggleSelect, isKeeper = false, onSetKeeper, similarity }: Props) {
```

(Add the new destructured params to the existing function signature — every existing param stays.)

Add, right after the existing `{meme.clusterId != null && (...)}`  block (so it renders below the
cluster-id row, above the existing "Flagged" checkbox row):

```tsx
      {selectable && (
        <div className="px-4 py-1 flex items-center gap-3 border-b">
          <label className="flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              role="checkbox"
              aria-label="Select"
              checked={selected}
              onChange={() => onToggleSelect?.()}
            />
            Select
          </label>
          {selected && (
            <label className="flex items-center gap-1 text-xs">
              <input
                type="radio"
                role="radio"
                aria-label="Keeper"
                checked={isKeeper}
                onChange={() => onSetKeeper?.()}
              />
              Keeper
            </label>
          )}
          {similarity != null && (
            <span className="text-xs text-gray-500 ml-auto">{Math.round(similarity * 100)}%</span>
          )}
        </div>
      )}
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd Frontend/memes-frontend && npx vitest run src/components/MemeCard.test.tsx
```

Expected: all tests in the file PASS, including every pre-existing test (the two existing
"Flagged" checkbox tests at lines 61/70 must still pass unmodified — this is the Global
Constraints regression check for this task).

- [ ] **Step 8: Type-check and lint**

```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/
```

Expected: both PASS with zero errors/warnings.

- [ ] **Step 9: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts Frontend/memes-frontend/src/test/mockApi.ts Frontend/memes-frontend/src/components/MemeCard.tsx Frontend/memes-frontend/src/components/MemeCard.test.tsx
git commit -m "feat: frontend API layer + MemeCard selection/keeper/similarity props"
```

---

### Task 6: `MemesDuplicatesList.tsx` — selection, two actions, select-all

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`
- Test: `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx`

**Interfaces:**
- Consumes: Task 5's `MemesApi.dismissDuplicateCluster(clusterId, memberIds?)`,
  `markImageIsFlagged(id, reason?)`, `getClusterSimilarity(clusterId)`, and `MemeCard`'s new props.

- [ ] **Step 1: Update the three existing tests whose call-shape assertions change**

In `Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx`, the existing flow ("click
'Not duplicates' directly, no selection step") no longer exists once Step 6 below ships — replace
these three tests' *interaction* (not just their assertions) to select-all-then-dismiss, matching
the new UI:

```typescript
  it('dismisses a cluster in place, showing member thumbnails and an inline undo button', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1)],
        facets: [], hasNext: false,
      }),
      dismissDuplicateCluster: vi.fn().mockResolvedValue({
        pairs: [{ image_id1: 'a', image_id2: 'b' }],
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select all' }))
    const button = await screen.findByRole('button', { name: 'Not duplicates' })
    fireEvent.click(button)

    await waitFor(() => {
      expect(api.dismissDuplicateCluster).toHaveBeenCalledWith(1, ['a', 'b'])
    })
    expect(await screen.findByText('Marked as not duplicates')).toBeInTheDocument()
    expect(screen.getByAltText('a')).toBeInTheDocument()
    expect(screen.getByAltText('b')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeInTheDocument()
  })
```

Apply the identical "select-all, then click" change to the second test (`'undoes a dismissal in
place and restores the row'`, currently at line ~259) and the third (`'keeps each dismissed
cluster independently undoable'`, currently at line ~283 — each cluster row in that test needs its
own "Select all" clicked before its "Not duplicates" click). Update each's
`toHaveBeenCalledWith(1)` / `toHaveBeenCalledWith(2)` to include the member id list, matching that
test's own fixture member ids.

- [ ] **Step 2: Write the new failing tests**

Append to the same file:

```typescript
describe('MemesDuplicatesList partial selection', () => {
  it('leaves the Not duplicates button disabled until 2+ members are selected', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 1)],
        facets: [], hasNext: false,
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    const notDuplicatesButton = await screen.findByRole('button', { name: 'Not duplicates' })
    expect(notDuplicatesButton).toBeDisabled()

    const checkboxes = await screen.findAllByRole('checkbox', { name: 'Select' })
    fireEvent.click(checkboxes[0])
    expect(notDuplicatesButton).toBeDisabled()

    fireEvent.click(checkboxes[1])
    expect(notDuplicatesButton).not.toBeDisabled()
  })

  it('dismisses only the selected subset, leaving unselected members visible', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 1)],
        facets: [], hasNext: false,
      }),
      dismissDuplicateCluster: vi.fn().mockResolvedValue({
        pairs: [{ image_id1: 'a', image_id2: 'b' }],
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    const checkboxes = await screen.findAllByRole('checkbox', { name: 'Select' })
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])
    fireEvent.click(await screen.findByRole('button', { name: 'Not duplicates' }))

    await waitFor(() => {
      expect(api.dismissDuplicateCluster).toHaveBeenCalledWith(1, ['a', 'b'])
    })
    // c was never selected -- the row is NOT fully collapsed, c's card is still rendered
    expect(screen.queryByText('Marked as not duplicates')).not.toBeInTheDocument()
    expect(screen.getByAltText('c')).toBeInTheDocument()
  })

  it('requires exactly one keeper before enabling "Duplicates — keep best"', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1)],
        facets: [], hasNext: false,
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    const checkboxes = await screen.findAllByRole('checkbox', { name: 'Select' })
    fireEvent.click(checkboxes[0])
    fireEvent.click(checkboxes[1])
    const keepBestButton = await screen.findByRole('button', { name: 'Duplicates — keep best' })
    expect(keepBestButton).toBeDisabled()

    const keeperRadios = await screen.findAllByRole('radio', { name: 'Keeper' })
    fireEvent.click(keeperRadios[0])
    expect(keepBestButton).not.toBeDisabled()
  })

  it('flags every selected non-keeper with reason duplicate_review, and undo unflags exactly those', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 1)],
        facets: [], hasNext: false,
      }),
      markImageIsFlagged: vi.fn().mockResolvedValue(undefined),
      unmarkImageIsFlagged: vi.fn().mockResolvedValue(undefined),
    })
    render(<MemesDuplicatesList memesApi={api} />)

    const checkboxes = await screen.findAllByRole('checkbox', { name: 'Select' })
    fireEvent.click(checkboxes[0])  // a
    fireEvent.click(checkboxes[1])  // b
    const keeperRadios = await screen.findAllByRole('radio', { name: 'Keeper' })
    fireEvent.click(keeperRadios[0])  // a is keeper
    fireEvent.click(await screen.findByRole('button', { name: 'Duplicates — keep best' }))

    await waitFor(() => {
      expect(api.markImageIsFlagged).toHaveBeenCalledWith('b', 'duplicate_review')
    })
    expect(api.markImageIsFlagged).not.toHaveBeenCalledWith('a', expect.anything())

    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }))
    await waitFor(() => {
      expect(api.unmarkImageIsFlagged).toHaveBeenCalledWith('b')
    })
    expect(api.unmarkImageIsFlagged).not.toHaveBeenCalledWith('a')
  })
})
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd Frontend/memes-frontend && npx vitest run src/components/MemesDuplicatesList.test.tsx
```

Expected: the three updated tests from Step 1 FAIL (no "Select all" checkbox exists yet); the four
new tests from Step 2 FAIL (none of the new UI exists yet).

- [ ] **Step 4: Implement**

In `Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx`, add new per-row state
alongside the existing `dismissedClusters` state:

```typescript
  const [selectedMembers, setSelectedMembers] = useState<Map<number, Set<string>>>(new Map())
  const [keeperByCluster, setKeeperByCluster] = useState<Map<number, string | undefined>>(new Map())
  const [flaggedMembers, setFlaggedMembers] = useState<Map<number, Set<string>>>(new Map())
  const [resolvedMembers, setResolvedMembers] = useState<Map<number, Set<string>>>(new Map())
```

(`flaggedMembers` tracks exactly which ids this component itself flagged via "keep best", per row
— needed so that row's Undo unflags exactly those and nothing else, per the spec's Non-goals
about not touching an unrelated manual flag. `resolvedMembers` tracks which member ids have been
removed from a row's active grid by either action, per row, so a partially-resolved row keeps
rendering its remaining members.)

Add selection/keeper toggle handlers:

```typescript
  const toggleSelect = useCallback((clusterId: number, memberId: string) => {
    setSelectedMembers(prev => {
      const next = new Map(prev)
      const set = new Set(next.get(clusterId) ?? [])
      if (set.has(memberId)) set.delete(memberId); else set.add(memberId)
      next.set(clusterId, set)
      return next
    })
  }, [])

  const selectAll = useCallback((clusterId: number, memberIds: string[]) => {
    setSelectedMembers(prev => new Map(prev).set(clusterId, new Set(memberIds)))
  }, [])

  const setKeeper = useCallback((clusterId: number, memberId: string) => {
    setKeeperByCluster(prev => new Map(prev).set(clusterId, memberId))
  }, [])
```

Add the two action handlers, replacing the existing `handleDismiss`:

```typescript
  const handleDismissSelected = useCallback(async (clusterId: number) => {
    const selected = Array.from(selectedMembers.get(clusterId) ?? [])
    if (selected.length < 2) return
    try {
      const response = await memesApi.dismissDuplicateCluster(clusterId, selected)
      setDismissedClusters(prev => new Map(prev).set(clusterId, response.pairs))
      setResolvedMembers(prev => new Map(prev).set(clusterId, new Set([...(prev.get(clusterId) ?? []), ...selected])))
    } catch {
      // Left silent, matching this component's existing error-handling convention.
    }
  }, [memesApi, selectedMembers])

  const handleKeepBest = useCallback(async (clusterId: number) => {
    const selected = Array.from(selectedMembers.get(clusterId) ?? [])
    const keeper = keeperByCluster.get(clusterId)
    if (selected.length < 2 || !keeper) return
    const losers = selected.filter(id => id !== keeper)
    await Promise.all(losers.map(id => memesApi.markImageIsFlagged(id, "duplicate_review")))
    setFlaggedMembers(prev => new Map(prev).set(clusterId, new Set([...(prev.get(clusterId) ?? []), ...losers])))
    setResolvedMembers(prev => new Map(prev).set(clusterId, new Set([...(prev.get(clusterId) ?? []), ...losers])))
  }, [memesApi, selectedMembers, keeperByCluster])

  const handleUndoFlagged = useCallback(async (clusterId: number) => {
    const flagged = Array.from(flaggedMembers.get(clusterId) ?? [])
    await Promise.all(flagged.map(id => memesApi.unmarkImageIsFlagged(id)))
    setFlaggedMembers(prev => { const next = new Map(prev); next.delete(clusterId); return next })
    setResolvedMembers(prev => {
      const next = new Map(prev)
      const set = new Set(next.get(clusterId) ?? [])
      flagged.forEach(id => set.delete(id))
      next.set(clusterId, set)
      return next
    })
  }, [memesApi, flaggedMembers])
```

Fetch similarity lazily per row the first time it renders — add a `similarityByCluster` state and
an effect-free lazy-fetch-on-first-render pattern (fetch inside `itemContent`'s row render is
wrong — React effects belong in `useEffect`; instead, fetch when a row's `clusterId` first appears
in `clusterRows`, via a `useEffect` keyed on `clusterRows`):

```typescript
  const [similarityByCluster, setSimilarityByCluster] = useState<Map<number, Map<string, number>>>(new Map())
  const fetchedSimilarityRef = useRef<Set<number>>(new Set())

  useEffect(() => {
    for (const row of clusterRows) {
      if (typeof row.clusterId !== "number") continue
      if (fetchedSimilarityRef.current.has(row.clusterId)) continue
      fetchedSimilarityRef.current.add(row.clusterId)
      memesApi.getClusterSimilarity(row.clusterId).then(response => {
        const map = new Map<string, number>()
        for (const pair of response.pairs) {
          map.set(`${pair.image_id1}:${pair.image_id2}`, pair.overlap)
        }
        setSimilarityByCluster(prev => new Map(prev).set(row.clusterId as number, map))
      }).catch(() => {
        // Presentation-only aid -- a failed fetch just leaves no badges for that row.
      })
    }
  }, [clusterRows, memesApi])
```

Replace the `itemContent` render body's non-dismissed branch (the `return (<div>... grid ...
"Not duplicates" button ...</div>)` block) with:

```tsx
          const clusterId = row.clusterId
          const resolved = typeof clusterId === "number" ? (resolvedMembers.get(clusterId) ?? new Set()) : new Set()
          const visibleMembers = row.members.filter(m => !resolved.has(m.id))
          const selected = typeof clusterId === "number" ? (selectedMembers.get(clusterId) ?? new Set()) : new Set()
          const keeper = typeof clusterId === "number" ? keeperByCluster.get(clusterId) : undefined
          const similarityMap = typeof clusterId === "number" ? similarityByCluster.get(clusterId) : undefined

          if (visibleMembers.length === 0 && typeof clusterId === "number" && flaggedMembers.has(clusterId)) {
            return (
              <div>
                <div className="py-3 flex items-center gap-3">
                  <span className="text-sm text-gray-400 italic">Marked as duplicates — kept best</span>
                  <button
                    className="text-xs rounded bg-gray-100 px-3 py-1 hover:bg-gray-200"
                    onClick={() => handleUndoFlagged(clusterId)}
                  >
                    Undo
                  </button>
                </div>
                <hr className="my-4 border-gray-300" />
              </div>
            )
          }

          return (
            <div>
              {typeof clusterId === "number" && (
                <label className="flex items-center gap-1 text-xs mb-2">
                  <input
                    type="checkbox"
                    aria-label="Select all"
                    checked={visibleMembers.length > 0 && visibleMembers.every(m => selected.has(m.id))}
                    onChange={() => selectAll(clusterId, visibleMembers.map(m => m.id))}
                  />
                  Select all
                </label>
              )}
              <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                {visibleMembers.map(meme => {
                  const others = visibleMembers.filter(m => m.id !== meme.id && selected.has(m.id))
                  const bestSim = others.length && similarityMap
                    ? Math.max(...others.map(o => similarityMap.get(`${[meme.id, o.id].sort().join(':')}`) ?? 0))
                    : undefined
                  return (
                    <MemeCard
                      key={meme.id}
                      meme={meme}
                      memesApi={memesApi}
                      onClick={() => setSelectedMeme(meme)}
                      selectable={typeof clusterId === "number"}
                      selected={selected.has(meme.id)}
                      onToggleSelect={() => typeof clusterId === "number" && toggleSelect(clusterId, meme.id)}
                      isKeeper={keeper === meme.id}
                      onSetKeeper={() => typeof clusterId === "number" && setKeeper(clusterId, meme.id)}
                      similarity={bestSim}
                    />
                  )
                })}
              </div>
              {typeof clusterId === "number" && (
                <div className="mt-2 flex gap-2">
                  <button
                    className="text-xs rounded bg-gray-100 px-3 py-1 hover:bg-gray-200 disabled:opacity-40"
                    disabled={selected.size < 2}
                    onClick={() => handleDismissSelected(clusterId)}
                  >
                    Not duplicates
                  </button>
                  <button
                    className="text-xs rounded bg-gray-100 px-3 py-1 hover:bg-gray-200 disabled:opacity-40"
                    disabled={selected.size < 2 || !keeper}
                    onClick={() => handleKeepBest(clusterId)}
                  >
                    Duplicates — keep best
                  </button>
                </div>
              )}
              <hr className="my-4 border-gray-300" />
            </div>
          )
```

(The similarity pair-key lookup, `[meme.id, o.id].sort().join(':')`, matches the ordering the new
`getClusterSimilarity` endpoint's response uses — `image_id1 < image_id2` string-sorted the same
way the backend's `min(a, b)`/`max(a, b)` UUID ordering does for two valid UUID strings.)

`handleDismiss`/`handleUndoCluster` (the pre-existing full-cluster handlers) stay as they are for
the fully-dismissed-row branch (`isDismissed` / `dismissedClusters` check at the top of
`itemContent`) — that branch is unchanged, since "select all" + "Not duplicates" now naturally
reproduces the exact same `dismissedClusters` state as before.

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd Frontend/memes-frontend && npx vitest run src/components/MemesDuplicatesList.test.tsx
```

Expected: all tests in the file PASS, including every other pre-existing test in the file not
touched by Step 1 (run the whole file).

- [ ] **Step 6: Type-check, lint, and run the full frontend suite**

```bash
cd Frontend/memes-frontend && npx tsc -b && npx eslint src/ && npx vitest run
```

Expected: all PASS with zero errors/warnings.

- [ ] **Step 7: Commit**

```bash
git add Frontend/memes-frontend/src/components/MemesDuplicatesList.tsx Frontend/memes-frontend/src/components/MemesDuplicatesList.test.tsx
git commit -m "feat: partial cluster selection UI — subset dismiss and keep-best actions"
```
