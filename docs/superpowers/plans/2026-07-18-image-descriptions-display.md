# Image Descriptions Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new read-only `GET /api/images/{image_id}/descriptions` endpoint and render its results in both the web app (`MemeDetails.tsx`) and the Android app (a new info-icon bottom sheet), so users can see the AI-generated description text that today only exists in the database.

**Architecture:** A new shared JSON schema (`imagedescription.schema.json`) drives codegen for the TypeScript type, the Kotlin DTO, and the Backend Pydantic model — the same three-way generation pipeline every other shared type already uses. `ImageRepository.get_descriptions` runs a direct SQLAlchemy query ordered by `prompt_key`; `ImageService.get_descriptions` maps rows straight to the response type (no branching logic, so — matching the existing `get_meme` precedent — it gets no dedicated service-level unit test, only router + repository coverage). Both clients fetch lazily alongside their existing per-image calls (`useFetchById` on web, a third parallel `viewModelScope.launch` on Android) and degrade silently to an empty-state message on any failure, exactly like the existing similar-images fetch on both platforms.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy async ORM (Backend), React / TypeScript / Vitest (Web), Kotlin / Jetpack Compose / MockK / Turbine (Android).

**Full design reference:** `docs/superpowers/specs/2026-07-18-image-descriptions-display-design.md`.

## Global Constraints

- Target Python is 3.11 (`.venv311`) — run all Backend commands with that venv active.
- Repositories must never call `session.commit()` — `get_async_db` owns commit/rollback for the Backend.
- ORM models live only in `Storage/models.py` — this plan adds no new models (reuses the existing `ImageDescription`).
- `backend_api.md` must stay in sync with routers — Task 3 updates it.
- Never combine `Backend/tests/` and `tests/integration/` in one `pytest` invocation — they have different `asyncio_mode` settings and must run as separate commands.
- `tests/integration/` needs `DATABASE_URL` set explicitly: `postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`.
- Frontend pre-commit gate: `tsc -b`, `eslint src/` (0 warnings), `vitest run` — and if `shared/schemas/` changed, regenerate types first and confirm `git diff` on the generated file is clean afterward.
- Android pre-commit gate: `.\gradlew :app:testDebugUnitTest --no-daemon` with `$env:JAVA_HOME` set to the Android Studio JBR.
- Windows dev: `WATCHFILES_FORCE_POLLING=1` is required for uvicorn `--reload` (relevant only if manually smoke-testing the Backend in Task 3).

---

### Task 1: Shared schema + regenerate types for all three clients

**Files:**
- Create: `shared/schemas/imagedescription.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Generated (verify, do not hand-edit): `Backend/app/types/generated/imagedescription.py`
- Generated (verify, do not hand-edit): `Frontend/memes-frontend/src/types/generated/all.d.ts`
- Generated (verify, do not hand-edit): `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`

**Interfaces:**
- Produces: shared type `ImageDescription { promptKey: string, text: string, modelUsed: string, createdAt: string }`, generated as `Backend/app/types/generated/imagedescription.py`'s `Schema`, `ImageDescription` in `Frontend/.../types/generated/all.d.ts`, and `ImageDescription` data class in `AndroidClient/.../data/model/Models.kt`.
- Consumed by: Task 2/3 (Backend), Task 4/5 (Web), Task 6/7/8 (Android).

- [ ] **Step 1: Create the schema file**

Create `shared/schemas/imagedescription.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "imagedescription.schema.json",
  "title": "ImageDescription",
  "type": "object",
  "properties": {
    "promptKey": {
      "type": "string",
      "description": "Identifies which configured prompt produced this description (see image_descriptions.prompts_file)"
    },
    "text": { "type": "string" },
    "modelUsed": {
      "type": "string",
      "description": "The Ollama model that generated this text"
    },
    "createdAt": { "type": "string" }
  },
  "required": ["promptKey", "text", "modelUsed", "createdAt"]
}
```

- [ ] **Step 2: Register it in `all.schema.json`**

In `shared/schemas/all.schema.json`, add a new entry to `definitions`, after the `"MemeSearchResponse"` entry:

```json
    "MemeSearchResponse": { "$ref": "memesearchresponse.schema.json" },
    "ImageDescription":    { "$ref": "imagedescription.schema.json" },
```

- [ ] **Step 3: Regenerate the Backend Pydantic type**

From the repo root, with `.venv311` active:

```bash
cd Backend
../.venv311/Scripts/datamodel-codegen.exe --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel
cd ..
```

Verify: `Backend/app/types/generated/imagedescription.py` now exists and defines a `Schema` class with `promptKey`, `text`, `modelUsed`, `createdAt` (all `str`, all required). Confirm no unrelated existing generated file changed content (only whitespace/timestamp-comment differences, if any, are acceptable — check with `git diff Backend/app/types/generated/`).

- [ ] **Step 4: Regenerate the Frontend TypeScript types**

```bash
cd Frontend
bash generate-types.sh
cd ..
```

Verify: `grep -n "ImageDescription" Frontend/memes-frontend/src/types/generated/all.d.ts` shows a new `export interface ImageDescription { promptKey: string; text: string; modelUsed: string; createdAt: string; }`-shaped block. Confirm `git diff Frontend/memes-frontend/src/types/generated/all.d.ts` shows only the new type added, nothing else changed.

- [ ] **Step 5: Regenerate the Android Kotlin DTOs**

```bash
python AndroidClient/scripts/generate_dtos.py
```

Verify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` now contains:

```kotlin
@Serializable
data class ImageDescription(
    @SerialName("promptKey") val promptKey: String,
    @SerialName("text") val text: String,
    @SerialName("modelUsed") val modelUsed: String,
    @SerialName("createdAt") val createdAt: String
)
```

Confirm `git diff AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` shows only this new data class added.

- [ ] **Step 6: Commit**

```bash
git add shared/schemas/imagedescription.schema.json shared/schemas/all.schema.json \
  Backend/app/types/generated/imagedescription.py \
  Frontend/memes-frontend/src/types/generated/all.d.ts \
  AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
git commit -m "feat: add ImageDescription shared schema and regenerate types"
```

---

### Task 2: Backend repository — `ImageRepository.get_descriptions`

**Files:**
- Modify: `Backend/app/repositories/image_repository.py`
- Modify: `tests/integration/test_backend_image_repository.py`

**Interfaces:**
- Consumes: `ImageDescription` ORM model (`Storage/models.py`, already imported in this file).
- Produces: `ImageRepository.get_descriptions(image_id: str) -> Sequence[Row]`, each row exposing `.prompt_key`, `.text`, `.model_used`, `.created_at`. Consumed by Task 3 (`ImageService.get_descriptions`).

- [ ] **Step 1: Write the failing tests**

In `tests/integration/test_backend_image_repository.py`, insert a new section right before the `# get_untagged / get_no_ocr` section (i.e. immediately after `test_has_description_embedding_true_when_present_false_otherwise`'s closing blank line, currently line 379):

```python
# --------------------------------------------------------------------------
# get_descriptions
# --------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_get_descriptions_returns_rows_ordered_by_prompt_key(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    await _insert_description(db_session, image, "humor_explanation", text="It's funny because...")
    await _insert_description(db_session, image, "general_description", text="A cat wearing a hat.")
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_descriptions(image.id)

    assert [r.prompt_key for r in rows] == ["general_description", "humor_explanation"]
    assert rows[0].text == "A cat wearing a hat."
    assert rows[0].model_used == "llava"
    assert rows[0].created_at is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_get_descriptions_returns_empty_list_when_none_exist(db_session):
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    db_session.add(image)
    await db_session.flush()

    repo = ImageRepository(db_session)
    rows = await repo.get_descriptions(image.id)

    assert rows == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v -k get_descriptions
```

Expected: FAIL — `AttributeError: 'ImageRepository' object has no attribute 'get_descriptions'`.

- [ ] **Step 3: Implement**

In `Backend/app/repositories/image_repository.py`, add this method immediately after `has_description_embedding` (before `get_meme_data`):

```python
    async def get_descriptions(self, image_id: str):
        result = await self.session.execute(
            select(
                ImageDescription.prompt_key,
                ImageDescription.text,
                ImageDescription.model_used,
                ImageDescription.created_at,
            )
            .where(ImageDescription.image_id == image_id)
            .order_by(ImageDescription.prompt_key)
        )
        return result.all()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_backend_image_repository.py -v
```

Expected: PASS (all tests in the file, including the 2 new ones — run the whole file to confirm nothing regressed).

- [ ] **Step 5: Commit**

```bash
git add Backend/app/repositories/image_repository.py tests/integration/test_backend_image_repository.py
git commit -m "feat: add ImageRepository.get_descriptions"
```

---

### Task 3: Backend service + router endpoint + docs

**Files:**
- Modify: `Backend/app/services/image_service.py`
- Modify: `Backend/app/api/images.py`
- Modify: `Backend/tests/test_images_endpoints.py`
- Modify: `backend_api.md`

**Interfaces:**
- Consumes: `ImageRepository.get_descriptions` (Task 2).
- Produces: `ImageService.get_descriptions(image_id: str) -> list[ImageDescription]`; `GET /api/images/{image_id}/descriptions` → `200 list[ImageDescription]` (empty list, never 404, when there are no rows).

- [ ] **Step 1: Write the failing router tests**

In `Backend/tests/test_images_endpoints.py`, add to the imports block (after the existing `Backend.app.types.generated.*` imports, line 23):

```python
from Backend.app.types.generated.imagedescription import Schema as ImageDescription
```

Add a new test class immediately after `TestGetSimilarImages` (i.e. right before `class TestGetMeme:`, currently line 528):

```python
class TestGetImageDescriptions:
    """Tests for GET /api/images/{image_id}/descriptions endpoint."""

    def test_get_image_descriptions_success(self, client, mock_image_service):
        mock_image_service.get_descriptions.return_value = [
            ImageDescription(
                promptKey="general_description",
                text="A cat wearing a hat.",
                modelUsed="qwen2.5vl:7b",
                createdAt="2026-07-18T12:00:00",
            )
        ]

        response = client.get("/api/images/123/descriptions")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["promptKey"] == "general_description"
        assert data[0]["text"] == "A cat wearing a hat."
        assert data[0]["modelUsed"] == "qwen2.5vl:7b"
        mock_image_service.get_descriptions.assert_called_once_with("123")

    def test_get_image_descriptions_empty(self, client, mock_image_service):
        mock_image_service.get_descriptions.return_value = []

        response = client.get("/api/images/456/descriptions")

        assert response.status_code == 200
        assert response.json() == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest Backend/tests/test_images_endpoints.py -v -k TestGetImageDescriptions
```

Expected: FAIL — `404 Not Found` (route doesn't exist yet), or an import error if `imagedescription` wasn't generated in Task 1.

- [ ] **Step 3: Implement — service layer**

In `Backend/app/services/image_service.py`, add the import (after the existing `Backend.app.types.generated.meme` import, line 15):

```python
from Backend.app.types.generated.imagedescription import Schema as ImageDescription
```

Add this method immediately after `get_meme` (before `get_similar`):

```python
    async def get_descriptions(self, image_id: str) -> list[ImageDescription]:
        rows = await self.repo.get_descriptions(image_id)
        return [
            ImageDescription(
                promptKey=prompt_key,
                text=text,
                modelUsed=model_used,
                createdAt=created_at.isoformat(),
            )
            for prompt_key, text, model_used, created_at in rows
        ]
```

- [ ] **Step 4: Implement — router layer**

In `Backend/app/api/images.py`, add the import (after the existing `Backend.app.types.generated.meme` import, line 18):

```python
from Backend.app.types.generated.imagedescription import Schema as ImageDescription
```

Add this route immediately after `get_similar_images` (before `get_meme`):

```python
@router.get("/{image_id}/descriptions", response_model=list[ImageDescription])
async def get_image_descriptions(
    image_id: str,
    response: Response,
    service: ImageService = Depends(get_image_service),
):
    response.headers.update(no_cache_headers())
    return await service.get_descriptions(image_id)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest Backend/tests/test_images_endpoints.py -v
```

Expected: PASS (all tests in the file, not just `TestGetImageDescriptions` — confirm nothing else regressed).

- [ ] **Step 6: Update `backend_api.md`**

In `backend_api.md`, add a new entry immediately after the existing `#### Get Similar Images` section (before `#### Get Meme Details`):

```markdown
#### Get Image Descriptions

Retrieve the AI-generated descriptions for a specific image (one entry per
configured prompt; see `image_descriptions.prompts_file`).

- **URL**: `/api/images/{image_id}/descriptions`
- **Method**: `GET`
- **Path Parameters**:
  - `image_id`: Unique identifier of the image
- **Response**: `ImageDescription[]` — `{ promptKey, text, modelUsed, createdAt }` per entry. An image with no descriptions yet returns `200 []`, never `404`.
- **Example**: `GET /api/images/abc123/descriptions`
```

- [ ] **Step 7: Commit**

```bash
git add Backend/app/services/image_service.py Backend/app/api/images.py Backend/tests/test_images_endpoints.py backend_api.md
git commit -m "feat: add GET /images/{id}/descriptions endpoint"
```

---

### Task 4: Web — API client method

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts`

**Interfaces:**
- Consumes: `GET /api/images/{id}/descriptions` (Task 3).
- Produces: `MemesApi.getDescriptions(id: string): Promise<ImageDescription[]>`. Consumed by Task 5 (`MemeDetails.tsx`).

- [ ] **Step 1: Add the method to the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, change the type import (line 1):

```typescript
import type { Concept, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse, TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse } from "../types/generated/all";
```

to:

```typescript
import type { Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse, TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse } from "../types/generated/all";
```

Add this method to the interface, immediately after `similarMemes` (line 14):

```typescript
  getDescriptions(id: string): Promise<ImageDescription[]>
```

- [ ] **Step 2: Implement in `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, change the type import (line 2) the same way:

```typescript
import type { Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse, TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse } from "../../types/generated/all"
```

Add this method immediately after `similarMemes` (after its closing brace, line 140):

```typescript
  async getDescriptions(id: string): Promise<ImageDescription[]> {
    const response = await fetch(
      `${this.baseUrl}/api/images/${id}/descriptions`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }
```

- [ ] **Step 3: Add the mock to `mockApi.ts`**

In `Frontend/memes-frontend/src/test/mockApi.ts`, add this line to the object returned by `makeMockApi` (after `similarMemes`, line 18):

```typescript
    getDescriptions: vi.fn().mockResolvedValue([]),
```

- [ ] **Step 4: Type-check**

```bash
cd Frontend/memes-frontend
tsc -b
cd ../..
```

Expected: no errors. `MemesApi` and `HttpMemesApi` both implement the new method with matching signatures, and `mockApi.ts`'s object satisfies `MemesApi` (the `as MemesApi` cast means a missing method wouldn't be caught here — Step 3 exists specifically so it's present regardless).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts Frontend/memes-frontend/src/test/mockApi.ts
git commit -m "feat: add getDescriptions to MemesApi and HttpMemesApi"
```

---

### Task 5: Web — render descriptions in `MemeDetails.tsx`

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.tsx`
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`

**Interfaces:**
- Consumes: `memesApi.getDescriptions` (Task 4).

- [ ] **Step 1: Write the failing tests**

In `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`, add a new `describe` block at the end of the outer `describe('MemeDetails', ...)`, after the `similarMemes` block (after its closing `})` on line 95, before the final `})` on line 96):

```typescript
  describe('descriptions', () => {
    it('renders each description with its humanized prompt label', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat wearing a hat.', modelUsed: 'qwen2.5vl:7b', createdAt: '2026-07-18T12:00:00' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByText('General description:')).toBeInTheDocument()
        expect(screen.getByText(/A cat wearing a hat\./)).toBeInTheDocument()
      })
    })

    it('renders multiple descriptions', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-18T12:00:00' },
          { promptKey: 'humor_explanation', text: 'Because cats.', modelUsed: 'llava', createdAt: '2026-07-18T12:00:00' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByText('General description:')).toBeInTheDocument()
        expect(screen.getByText('Humor explanation:')).toBeInTheDocument()
      })
    })

    it('shows a quiet empty state when there are no descriptions', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([]),
      })
      await waitFor(() => {
        expect(screen.getByText('No description available')).toBeInTheDocument()
      })
    })
  })
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd Frontend/memes-frontend
vitest run src/components/MemeDetails.test.tsx
cd ../..
```

Expected: FAIL — the new text is never rendered (no descriptions section exists yet).

- [ ] **Step 3: Implement**

In `Frontend/memes-frontend/src/components/MemeDetails.tsx`, change the type import (line 5):

```typescript
import type { Concept, Meme } from "../types/generated/all"
```

to:

```typescript
import type { Concept, ImageDescription, Meme } from "../types/generated/all"
```

Add a `descriptions` state variable, immediately after `concepts` (line 22):

```typescript
  const [descriptions, setDescriptions] = useState<ImageDescription[]>([])
```

Add the fetch, immediately after the `concepts` fetch (line 37):

```typescript
  useFetchById(meme.id, id => memesApi.getDescriptions(id), setDescriptions)
```

Add a humanization helper above the `MemeDetails` component (after the constant declarations, before `export function MemeDetails`):

```typescript
function humanizePromptKey(promptKey: string): string {
  const spaced = promptKey.replace(/_/g, " ")
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
```

Add the rendered section between the "Text Lines" `<div>` and the "Tags" `<div>` (i.e. between lines 181 and 183):

```tsx
        <div>
          <strong>Descriptions:</strong>
          {descriptions.length === 0 ? (
            <p className="text-gray-400">No description available</p>
          ) : (
            <ul className="ml-2 space-y-2">
              {descriptions.map(d => (
                <li key={d.promptKey}>
                  <span className="font-medium">{humanizePromptKey(d.promptKey)}:</span> {d.text}
                </li>
              ))}
            </ul>
          )}
        </div>
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd Frontend/memes-frontend
vitest run src/components/MemeDetails.test.tsx
cd ../..
```

Expected: PASS (all tests in the file, not just the new `descriptions` block).

- [ ] **Step 5: Full frontend pre-commit gate**

```bash
cd Frontend/memes-frontend
tsc -b
eslint src/
vitest run
cd ../..
```

Expected: all three pass with 0 lint warnings.

- [ ] **Step 6: Commit**

```bash
git add Frontend/memes-frontend/src/components/MemeDetails.tsx Frontend/memes-frontend/src/components/MemeDetails.test.tsx
git commit -m "feat: render image descriptions in MemeDetails"
```

---

### Task 6: Android — API service + repository method

**Files:**
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt`

**Interfaces:**
- Consumes: `GET /api/images/{id}/descriptions` (Task 3), `ImageDescription` DTO (Task 1).
- Produces: `MemeApiService.getDescriptions(id: String): List<ImageDescription>`; `MemeRepository.getDescriptions(id: String): Result<List<ImageDescription>>`. Consumed by Task 7 (`MemeDetailViewModel`).

No dedicated test file exists for either class today (`getSimilarMemes` follows the same thin-wrapper pattern with no repository-level test — it's exercised indirectly through `MemeDetailViewModel` tests instead, which Task 7 covers). Verification for this task is compilation only.

- [ ] **Step 1: Add the endpoint to `MemeApiService`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt`, add the import (after `import com.memebrowser.app.data.model.HealthResponse`, line 3):

```kotlin
import com.memebrowser.app.data.model.ImageDescription
```

Add this method immediately after `getSimilarMemes` (line 51):

```kotlin
    @GET("api/images/{id}/descriptions")
    suspend fun getDescriptions(@Path("id") id: String): List<ImageDescription>
```

- [ ] **Step 2: Add the wrapper to `MemeRepository`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt`, add the import (after `import com.memebrowser.app.data.model.HealthResponse`, line 5):

```kotlin
import com.memebrowser.app.data.model.ImageDescription
```

Add this method immediately after `getSimilarMemes` (line 74):

```kotlin
    suspend fun getDescriptions(id: String): Result<List<ImageDescription>> = runCatching {
        api.getDescriptions(id)
    }
```

- [ ] **Step 3: Compile check**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:compileDebugKotlin --no-daemon
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Commit**

```bash
git add AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt
git commit -m "feat: add getDescriptions to MemeApiService and MemeRepository"
```

---

### Task 7: Android — wire into `MemeDetailViewModel`

**Files:**
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt`
- Modify: `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`

**Interfaces:**
- Consumes: `MemeRepository.getDescriptions` (Task 6).
- Produces: `DetailUiState.descriptions: List<ImageDescription>`. Consumed by Task 8 (`MemeDetailScreen`).

- [ ] **Step 1: Write the failing tests**

In `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`, add the import (after `import com.memebrowser.app.data.model.HealthResponse`, line 5):

```kotlin
import com.memebrowser.app.data.model.ImageDescription
```

In `setup()`, add a default stub (after the existing `getSimilarMemes` stub, line 44):

```kotlin
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(emptyList())
```

Add two new tests at the end of the class, after `duplicate similar IDs are deduplicated` (after its closing `}` on line 170, before the class's final `}` on line 171):

```kotlin
    @Test
    fun `descriptions are populated in state`() = runTest {
        val descriptions = listOf(
            ImageDescription(promptKey = "general_description", text = "A cat.", modelUsed = "llava", createdAt = "2026-07-18T12:00:00")
        )
        coEvery { repo.getDescriptions("meme-1") } returns Result.success(descriptions)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertEquals(1, state.descriptions.size)
            assertEquals("general_description", state.descriptions[0].promptKey)
        }
    }

    @Test
    fun `getDescriptions failure is silent and does not set error`() = runTest {
        coEvery { repo.getDescriptions("meme-1") } returns Result.failure(Exception("network error"))
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)
        viewModel.state.test {
            val state = awaitItem()
            assertTrue(state.descriptions.isEmpty())
            assertNull(state.error)
        }
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:testDebugUnitTest --no-daemon --tests "com.memebrowser.app.ui.detail.MemeDetailViewModelTest"
```

Expected: FAIL — compilation error (`descriptions` is not a member of `DetailUiState`, `getDescriptions` is not a member of `MemeRepository`'s mock surface for this call) or `MockKException` since the stub doesn't match any real invocation yet.

- [ ] **Step 3: Implement**

In `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt`, add the import (after `import com.memebrowser.app.data.model.Meme`, line 8):

```kotlin
import com.memebrowser.app.data.model.ImageDescription
```

Add a field to `DetailUiState`:

```kotlin
data class DetailUiState(
    val meme: Meme? = null,
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val saveSuccess: Boolean = false,
    val similarMemes: List<Meme> = emptyList(),
    val isLoadingSimilar: Boolean = false,
    val descriptions: List<ImageDescription> = emptyList()
)
```

Add the call in `init`:

```kotlin
    init {
        loadMeme()
        loadSimilar()
        loadDescriptions()
    }
```

Add the loader function, immediately after `loadSimilar`:

```kotlin
    private fun loadDescriptions() {
        viewModelScope.launch {
            repo.getDescriptions(memeId)
                .onSuccess { descriptions -> _state.update { it.copy(descriptions = descriptions) } }
                .onFailure { /* silent — supplementary content, matches loadSimilar's failure handling */ }
        }
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:testDebugUnitTest --no-daemon --tests "com.memebrowser.app.ui.detail.MemeDetailViewModelTest"
```

Expected: BUILD SUCCESSFUL, all tests in the class pass.

- [ ] **Step 5: Commit**

```bash
git add AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt \
  AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt
git commit -m "feat: load image descriptions in MemeDetailViewModel"
```

---

### Task 8: Android — descriptions bottom sheet UI

**Files:**
- Create: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/DescriptionsBottomSheet.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt`

**Interfaces:**
- Consumes: `DetailUiState.descriptions` (Task 7).
- Produces: `DescriptionsBottomSheet(descriptions: List<ImageDescription>, onDismiss: () -> Unit)` composable.

This task is UI-only with no branching logic beyond what Task 7 already covers, and the existing `androidTest` instrumented UI test for this screen requires a connected device/emulator not available in this environment — per this repo's precedent for unrunnable manual/device steps (see CLAUDE.md's "Known gotchas"), do not skip verification silently: run the compile check in Step 4, and if a device is available, do the manual check in Step 5.

- [ ] **Step 1: Create the bottom sheet composable**

Create `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/DescriptionsBottomSheet.kt`:

```kotlin
package com.memebrowser.app.ui.detail

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.memebrowser.app.data.model.ImageDescription

private fun humanizePromptKey(promptKey: String): String {
    val spaced = promptKey.replace('_', ' ')
    return spaced.replaceFirstChar { it.uppercase() }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DescriptionsBottomSheet(
    descriptions: List<ImageDescription>,
    onDismiss: () -> Unit
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp)
                .padding(bottom = 32.dp)
        ) {
            Text(
                text = "Description",
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(bottom = 8.dp)
            )
            if (descriptions.isEmpty()) {
                Text(
                    text = "No description available",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            } else {
                descriptions.forEach { description ->
                    Text(
                        text = humanizePromptKey(description.promptKey),
                        style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.padding(top = 8.dp)
                    )
                    Text(
                        text = description.text,
                        style = MaterialTheme.typography.bodyMedium
                    )
                }
            }
        }
    }
}
```

- [ ] **Step 2: Wire the info icon and sheet state into `MemeDetailScreen`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt`, add the import (after `import androidx.compose.material.icons.filled.Download`, line 29):

```kotlin
import androidx.compose.material.icons.filled.Info
```

Add sheet-visibility state, immediately after `var toolbarVisible by remember { mutableStateOf(true) }` (line 73):

```kotlin
    var showDescriptions by remember { mutableStateOf(false) }
```

Update the `BottomActionBar(...)` call (lines 127-137) to pass the two new parameters:

```kotlin
            state.meme?.let { meme ->
                BottomActionBar(
                    meme = meme,
                    isSaving = state.isSaving,
                    onSave = { viewModel.saveToGallery(context) },
                    onShare = { viewModel.share(context) },
                    onToggleFlagged = { viewModel.toggleFlagged() },
                    similarMemes = state.similarMemes,
                    isLoadingSimilar = state.isLoadingSimilar,
                    onSimilarMemeClick = onNavigateToMeme,
                    onTagClick = onTagClick,
                    onInfoClick = { showDescriptions = true }
                )
            }
```

Add the sheet itself, immediately after the `AnimatedVisibility` block that renders `BottomActionBar` closes (after its closing `}` on line 139, before `SnackbarHost` on line 141):

```kotlin
        if (showDescriptions) {
            DescriptionsBottomSheet(
                descriptions = state.descriptions,
                onDismiss = { showDescriptions = false }
            )
        }
```

Update the `BottomActionBar` function signature to accept the new callback:

```kotlin
@Composable
private fun BottomActionBar(
    meme: Meme,
    isSaving: Boolean,
    onSave: () -> Unit,
    onShare: () -> Unit,
    onToggleFlagged: () -> Unit,
    similarMemes: List<Meme>,
    isLoadingSimilar: Boolean,
    onSimilarMemeClick: (String) -> Unit,
    onTagClick: (category: String, value: String) -> Unit,
    onInfoClick: () -> Unit
) {
```

Add the info icon to the icon `Row` (after the existing flagged `IconButton`'s closing `}`, before the `Row`'s closing `}`, currently around line 224):

```kotlin
            IconButton(onClick = onInfoClick) {
                Icon(Icons.Default.Info, contentDescription = "Description", tint = Color.White)
            }
```

- [ ] **Step 3: Compile check**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:compileDebugKotlin --no-daemon
```

Expected: BUILD SUCCESSFUL.

- [ ] **Step 4: Full Android pre-commit gate**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:testDebugUnitTest --no-daemon
```

Expected: BUILD SUCCESSFUL, all unit tests pass (including Task 7's new tests).

- [ ] **Step 5: Manual verification (requires a connected device/emulator — not runnable in this sandboxed environment; do not skip silently, note if it can't be run here)**

Install and open the app, navigate to any image detail screen, tap the new info icon, and confirm:
- The bottom sheet opens showing "Description" as the title.
- If the image has a `general_description` row in the database, it renders as "General description" followed by the text.
- If the image has no description rows, it shows "No description available".
- Dismissing the sheet (swipe down or tap outside) closes it and the icon can be tapped again.

- [ ] **Step 6: Commit**

```bash
git add AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/DescriptionsBottomSheet.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt
git commit -m "feat: add descriptions bottom sheet to the Android detail screen"
```

---

## Final verification (after all tasks)

```bash
# Backend
pytest Backend/tests/ -q
DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -q

# Frontend (from Frontend/memes-frontend/)
tsc -b
eslint src/
vitest run
```

```powershell
# Android
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew :app:testDebugUnitTest --no-daemon
```

Run the Backend commands as two separate invocations (different `asyncio_mode` settings — see CLAUDE.md's "Known gotchas").

Then, per this repo's pre-commit checklist:
- Confirm the Backend server starts without import errors: `uvicorn Backend.app.main:app --reload --reload-dir Backend/app --env-file environments/.env.general --port 8082` (with `WATCHFILES_FORCE_POLLING=1` set).
- Hit `GET /api/diagnostics/health`, `GET /api/images?limit=1`, and `GET /api/images/{some_id}/descriptions` manually against a real `general`-environment image that has a populated description, and verify the response shape matches `backend_api.md`.
- Confirm `git diff` on all three generated-types files (`Backend/app/types/generated/`, `Frontend/memes-frontend/src/types/generated/all.d.ts`, `AndroidClient/.../data/model/Models.kt`) is clean relative to what was committed in Task 1 — no drift from re-running codegen.
