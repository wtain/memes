# Similarity Mode Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-view "Visual" / "Semantic" toggle to the existing similar-images section on both Web and Android, wiring the already-implemented `GET /api/images/{id}/similar?source=image|description` backend parameter into both clients' UIs.

**Architecture:** No backend changes. Web: `MemeDetails.tsx` gains a `similarSource` state var, threaded into a composite-key `useFetchById` call (so switching modes triggers a refetch without modifying the shared `useFetchById` hook); a small tab UI switches it, and it naturally carries over across images since it lives in the same component instance. Android: `MemeRepository`/`MemeApiService` gain a `source` parameter (default `"image"`, keeping the existing call site compiling unchanged); `MemeDetailViewModel` reads an initial value from `SavedStateHandle["source"]` and exposes `setSimilarSource()`; carry-over across the "tap a similar thumbnail to open another detail screen" navigation is done via a new optional `source` route argument in `NavGraph.kt`.

**Tech Stack:** React / TypeScript / Vitest (Web), Kotlin / Jetpack Compose / MockK / Turbine / Navigation Compose (Android).

**Full design reference:** `docs/superpowers/specs/2026-07-18-similarity-mode-toggle-design.md`.

## Global Constraints

- No backend changes in this plan — the `source` query parameter and its 404 behavior already exist and are documented in `backend_api.md`.
- Neither platform should distinguish "no embedding for this image" (404) from "embedding exists but nothing similar found" (200, empty `items`) — both must present as the same mode-specific empty state, relying on each platform's existing silent-failure-to-empty-list conventions (`useFetchById`'s swallowed catch on Web, `runCatching`/`onFailure` on Android).
- Default mode on first load (no carried-over selection) is **Visual** (`"image"`).
- Empty-state text: **"No similar images found"** (Visual) / **"No semantic similarity available for this image yet"** (Semantic) — exact strings, both platforms.
- Frontend pre-commit gate: `tsc -b`, `eslint src/` (0 warnings), `vitest run` must all pass.
- Android pre-commit gate: `.\gradlew :app:testDebugUnitTest --no-daemon` with `$env:JAVA_HOME` set to the Android Studio JBR.
- Never combine `Backend/tests/` and `tests/integration/` in one `pytest` invocation — not relevant to this plan (no Backend files touched), noted only because it's a standing repo-wide rule.

---

### Task 1: Web — API client method

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`

**Interfaces:**
- Produces: `MemesApi.similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse>`. Consumed by Task 2 (`MemeDetails.tsx`).

This is a non-breaking, additive change — `source` is optional and no existing call site is touched in this task, so nothing in `MemeDetails.tsx` or its tests needs updating yet.

- [ ] **Step 1: Update the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, change line 14 from:

```typescript
  similarMemes(id: string): Promise<MemeSearchResponse>
```

to:

```typescript
  similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse>
```

- [ ] **Step 2: Update `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, replace the `similarMemes` method (lines 125-140):

```typescript
  async similarMemes(id: string): Promise<MemeSearchResponse> {
    const response = await fetch(
      `${this.baseUrl}/api/images/${id}/similar`,
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

with:

```typescript
  async similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse> {
    const params = source ? `?source=${source}` : ""
    const response = await fetch(
      `${this.baseUrl}/api/images/${id}/similar${params}`,
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

- [ ] **Step 3: Type-check**

```bash
cd Frontend/memes-frontend
tsc -b
cd ../..
```

Expected: no errors. `mockApi.ts`'s existing `similarMemes: vi.fn().mockResolvedValue(...)` mock still satisfies the interface unchanged (mock functions don't enforce arity).

- [ ] **Step 4: Run the full frontend test suite to confirm no regressions**

```bash
cd Frontend/memes-frontend
vitest run
cd ../..
```

Expected: PASS, same test count as before this change (this task doesn't touch any call site, so no existing assertions should be affected).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts
git commit -m "feat: add optional source param to MemesApi.similarMemes"
```

---

### Task 2: Web — toggle UI + empty state in MemeDetails.tsx

**Files:**
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.tsx`
- Modify: `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`

**Interfaces:**
- Consumes: `memesApi.similarMemes(id, source)` (Task 1).

- [ ] **Step 1: Update existing test assertions, then write the new failing tests**

In `Frontend/memes-frontend/src/components/MemeDetails.test.tsx`, the `describe('similarMemes', ...)` block's two existing tests assert `toHaveBeenCalledWith` with a single argument. Once the component always passes `source` explicitly, these need the second argument. Change:

```typescript
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
```

to:

```typescript
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "image")
```

and:

```typescript
      expect(api.similarMemes).toHaveBeenCalledWith('meme-B')
```

to:

```typescript
      expect(api.similarMemes).toHaveBeenCalledWith('meme-B', "image")
```

Then add a new `describe` block, after the `descriptions` block (after its closing `})` on line 131, before the outer `describe`'s final `})` on line 132):

```typescript
  describe('similarity mode toggle', () => {
    it('defaults to Visual and fetches with source=image', async () => {
      const { api } = renderMemeDetails()
      await act(async () => {})
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "image")
      expect(screen.getByRole('button', { name: 'Visual' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Semantic' })).toBeInTheDocument()
    })

    it('clicking Semantic refetches with source=description', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn()
          .mockResolvedValueOnce({ items: [], facets: [], hasNext: false })
          .mockResolvedValueOnce({ items: [{ id: 'sem-1', imageUrl: '/sem-1.jpg' }], facets: [], hasNext: false }),
      })
      render(
        <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "description")
      expect(api.similarMemes).toHaveBeenCalledTimes(2)
    })

    it('shows the Visual empty-state message when there are no visual matches', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
      })
      await waitFor(() => {
        expect(screen.getByText('No similar images found')).toBeInTheDocument()
      })
    })

    it('shows the Semantic empty-state message when there are no semantic matches', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
      })
      render(
        <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      expect(screen.getByText('No semantic similarity available for this image yet')).toBeInTheDocument()
    })

    it('carries the selected mode over when the meme changes', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
      })
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(
        <MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      expect(api.similarMemes).toHaveBeenCalledWith('meme-B', "description")
    })
  })
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd Frontend/memes-frontend
vitest run src/components/MemeDetails.test.tsx
cd ../..
```

Expected: FAIL — the two updated existing assertions fail (component still calls `similarMemes` with one argument), and the new tests fail (no "Visual"/"Semantic" buttons exist yet, no empty-state messages rendered).

- [ ] **Step 3: Implement**

In `Frontend/memes-frontend/src/components/MemeDetails.tsx`, add state, immediately after `similarMemes` (line 26):

```typescript
  const [similarSource, setSimilarSource] = useState<"image" | "description">("image")
```

Change the `similarMemes` fetch (line 42) from:

```typescript
  useFetchById(meme.id, id => memesApi.similarMemes(id), resp => setSimilarMemes(resp.items ?? []))
```

to:

```typescript
  useFetchById(
    `${meme.id}:${similarSource}`,
    () => memesApi.similarMemes(meme.id, similarSource),
    resp => setSimilarMemes(resp.items ?? []),
  )
```

Replace the similar-images grid section (lines 240-249):

```tsx
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {similarMemes.map(m => (
            <div key={m.id}>
              <MemeCard meme={m} memesApi={memesApi} onClick={() => navigate(`/memes/${m.id}`)} />
              {typeof m.cosineDistance === "number" && (
                <p className="text-center text-xs text-gray-400 mt-1">{m.cosineDistance.toFixed(2)}</p>
              )}
            </div>
          ))}
        </div>
```

with:

```tsx
        <div>
          <div className="flex gap-2 mb-2">
            <button
              onClick={() => setSimilarSource("image")}
              className={`px-3 py-1 text-xs rounded border ${similarSource === "image" ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 text-gray-600 hover:bg-gray-100"}`}
            >
              Visual
            </button>
            <button
              onClick={() => setSimilarSource("description")}
              className={`px-3 py-1 text-xs rounded border ${similarSource === "description" ? "bg-gray-800 text-white border-gray-800" : "border-gray-300 text-gray-600 hover:bg-gray-100"}`}
            >
              Semantic
            </button>
          </div>
          {similarMemes.length === 0 ? (
            <p className="text-gray-400 text-sm">
              {similarSource === "description"
                ? "No semantic similarity available for this image yet"
                : "No similar images found"}
            </p>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              {similarMemes.map(m => (
                <div key={m.id}>
                  <MemeCard meme={m} memesApi={memesApi} onClick={() => navigate(`/memes/${m.id}`)} />
                  {typeof m.cosineDistance === "number" && (
                    <p className="text-center text-xs text-gray-400 mt-1">{m.cosineDistance.toFixed(2)}</p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd Frontend/memes-frontend
vitest run src/components/MemeDetails.test.tsx
cd ../..
```

Expected: PASS (all tests in the file, not just the new block).

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
git commit -m "feat: add Visual/Semantic similarity toggle to MemeDetails"
```

---

### Task 3: Android — API/repository/ViewModel wiring

**Files:**
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt`
- Modify: `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`

**Interfaces:**
- Produces: `MemeApiService.getSimilarMemes(id, source = "image")`; `MemeRepository.getSimilarMemes(id, source): Result<List<Meme>>`; `DetailUiState.similarSource: String`; `MemeDetailViewModel.setSimilarSource(source: String)`. Consumed by Task 4 (`MemeDetailScreen`/`BottomActionBar`/`NavGraph`).

`source` gets a default value (`"image"`) at the `MemeApiService`/`MemeRepository` layer specifically so this task's own changes keep compiling before `MemeDetailViewModel`'s call site is updated later in this same task — both layers change together here, so this is really about keeping the diff readable in commit order, not a compile boundary between tasks.

- [ ] **Step 1: Write the failing tests**

In `AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt`, every existing `coEvery { repo.getSimilarMemes("meme-1") }` stub must gain the explicit `"image"` argument, since production code will now always pass `source` explicitly. Update all 5 occurrences:

In `setup()` (line 45):
```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.success(emptyList())
```
→
```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(emptyList())
```

In `similar memes are populated in state` (line 120):
```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.success(similar)
```
→
```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(similar)
```

In `getSimilarMemes failure is silent and does not set error` (line 132):
```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.failure(Exception("network error"))
```
→
```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.failure(Exception("network error"))
```

In `current meme is excluded from similar results` (line 149):
```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.success(withSelf)
```
→
```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(withSelf)
```

In `duplicate similar IDs are deduplicated` (line 165):
```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.success(withDuplicates)
```
→
```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(withDuplicates)
```

Then add two new tests at the end of the class, after `getDescriptions failure is silent and does not set error` (after its closing `}` on line 197, before the class's final `}` on line 198):

```kotlin
    @Test
    fun `setSimilarSource refetches with the new source`() = runTest {
        val visual = listOf(fakeMeme.copy(id = "visual-1"))
        val semantic = listOf(fakeMeme.copy(id = "semantic-1"))
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(visual)
        coEvery { repo.getSimilarMemes("meme-1", "description") } returns Result.success(semantic)
        viewModel = MemeDetailViewModel(savedStateHandle, repo, envRepo)

        viewModel.setSimilarSource("description")

        viewModel.state.test {
            val state = awaitItem()
            assertEquals("description", state.similarSource)
            assertEquals(1, state.similarMemes.size)
            assertEquals("semantic-1", state.similarMemes[0].id)
        }
        coVerify(exactly = 1) { repo.getSimilarMemes("meme-1", "description") }
    }

    @Test
    fun `initial similarSource is read from the source SavedStateHandle argument`() = runTest {
        val handleWithSource = SavedStateHandle(mapOf("memeId" to "meme-1", "source" to "description"))
        coEvery { repo.getSimilarMemes("meme-1", "description") } returns Result.success(emptyList())
        viewModel = MemeDetailViewModel(handleWithSource, repo, envRepo)

        viewModel.state.test {
            val state = awaitItem()
            assertEquals("description", state.similarSource)
        }
        coVerify(exactly = 1) { repo.getSimilarMemes("meme-1", "description") }
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:testDebugUnitTest --no-daemon --tests "com.memebrowser.app.ui.detail.MemeDetailViewModelTest"
```

Expected: FAIL — compilation error (`getSimilarMemes` doesn't accept a second argument yet, `setSimilarSource` and `similarSource` don't exist yet).

- [ ] **Step 3: Implement — `MemeApiService`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt`, change:

```kotlin
    @GET("api/images/{id}/similar")
    suspend fun getSimilarMemes(@Path("id") id: String): MemeSearchResponse
```

to:

```kotlin
    @GET("api/images/{id}/similar")
    suspend fun getSimilarMemes(@Path("id") id: String, @Query("source") source: String = "image"): MemeSearchResponse
```

- [ ] **Step 4: Implement — `MemeRepository`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt`, change:

```kotlin
    suspend fun getSimilarMemes(id: String): Result<List<Meme>> = runCatching {
        api.getSimilarMemes(id).items ?: emptyList()
    }
```

to:

```kotlin
    suspend fun getSimilarMemes(id: String, source: String = "image"): Result<List<Meme>> = runCatching {
        api.getSimilarMemes(id, source).items ?: emptyList()
    }
```

- [ ] **Step 5: Implement — `MemeDetailViewModel`**

Add `similarSource` to `DetailUiState`:

```kotlin
data class DetailUiState(
    val meme: Meme? = null,
    val isLoading: Boolean = false,
    val isSaving: Boolean = false,
    val error: String? = null,
    val saveSuccess: Boolean = false,
    val similarMemes: List<Meme> = emptyList(),
    val isLoadingSimilar: Boolean = false,
    val similarSource: String = "image",
    val descriptions: List<ImageDescription> = emptyList()
)
```

Change the `memeId` line and add a `source` read immediately after it:

```kotlin
    private val memeId: String = checkNotNull(savedStateHandle["memeId"])
    private val initialSimilarSource: String = savedStateHandle["source"] ?: "image"

    private val _state = MutableStateFlow(DetailUiState(similarSource = initialSimilarSource))
```

(This replaces the previous `private val _state = MutableStateFlow(DetailUiState())` line.)

Replace `loadSimilar`:

```kotlin
    private fun loadSimilar() {
        viewModelScope.launch {
            val source = _state.value.similarSource
            _state.update { it.copy(isLoadingSimilar = true) }
            repo.getSimilarMemes(memeId, source)
                .onSuccess { memes ->
                    val deduped = memes.filter { it.id != memeId }.distinctBy { it.id }
                    _state.update { it.copy(similarMemes = deduped, isLoadingSimilar = false) }
                }
                .onFailure { _state.update { it.copy(similarMemes = emptyList(), isLoadingSimilar = false) } }
        }
    }
```

(Only change from before: `repo.getSimilarMemes(memeId)` → `repo.getSimilarMemes(memeId, source)`, and `onFailure` now also resets `similarMemes` to empty — needed so a failed refetch after switching modes doesn't keep showing the previous mode's stale results.)

Add a new public function, immediately after `toggleFlagged` (after its closing `}`):

```kotlin
    fun setSimilarSource(source: String) {
        _state.update { it.copy(similarSource = source) }
        loadSimilar()
    }
```

- [ ] **Step 6: Run tests to verify they pass**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:testDebugUnitTest --no-daemon --tests "com.memebrowser.app.ui.detail.MemeDetailViewModelTest"
```

Expected: BUILD SUCCESSFUL, all tests in the class pass (11 existing + 2 new = 13).

- [ ] **Step 7: Commit**

```bash
git add AndroidClient/app/src/main/java/com/memebrowser/app/data/api/MemeApiService.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/data/repository/MemeRepository.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailViewModel.kt \
  AndroidClient/app/src/test/java/com/memebrowser/app/ui/detail/MemeDetailViewModelTest.kt
git commit -m "feat: add multi-mode similarity to MemeApiService/MemeRepository/MemeDetailViewModel"
```

---

### Task 4: Android — toggle UI + navigation carry-over

**Files:**
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt`
- Modify: `AndroidClient/app/src/main/java/com/memebrowser/app/ui/NavGraph.kt`
- Modify: `AndroidClient/app/src/androidTest/java/com/memebrowser/app/ui/detail/MemeDetailScreenTest.kt`

**Interfaces:**
- Consumes: `DetailUiState.similarSource`, `MemeDetailViewModel.setSimilarSource` (Task 3).

This task has no new automated tests of its own (UI wiring with no new branching logic beyond Task 3's, matching the same acceptance bar as spec 1's equivalent UI task) — the acceptance bar is: compiles clean (including the `androidTest` source set, which `testDebugUnitTest` does **not** compile — see Step 4), and the full `testDebugUnitTest` suite (including Task 3's tests) still passes. Actually running the instrumented test and manual device verification cannot be done in a sandboxed environment without a connected device/emulator — note this in the report rather than skipping silently.

- [ ] **Step 1: Add the chip toggle and empty-state message to `BottomActionBar`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt`, add imports (alongside the existing `androidx.compose.material3.*` imports):

```kotlin
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
```

Change `BottomActionBar`'s signature (currently lines 161-172):

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

to:

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
    similarSource: String,
    onSimilarSourceChange: (String) -> Unit,
    onSimilarMemeClick: (id: String, source: String) -> Unit,
    onTagClick: (category: String, value: String) -> Unit,
    onInfoClick: () -> Unit
) {
```

Replace the similar-images block (currently lines 184-205):

```kotlin
        if (isLoadingSimilar || similarMemes.isNotEmpty()) {
            LazyRow(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                if (isLoadingSimilar) {
                    item { CircularProgressIndicator(modifier = Modifier.size(24.dp)) }
                } else {
                    items(similarMemes, key = { it.id }) { similar ->
                        AsyncImage(
                            model = "http://localhost${similar.imageUrl}",
                            contentDescription = similar.originalFileName,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .size(80.dp)
                                .clip(RoundedCornerShape(4.dp))
                                .clickable { onSimilarMemeClick(similar.id) }
                        )
                    }
                }
            }
        }
```

with:

```kotlin
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp)
        ) {
            FilterChip(
                selected = similarSource == "image",
                onClick = { onSimilarSourceChange("image") },
                label = { Text("Visual", style = MaterialTheme.typography.labelSmall) },
                colors = FilterChipDefaults.filterChipColors(labelColor = Color.White, selectedLabelColor = Color.Black)
            )
            FilterChip(
                selected = similarSource == "description",
                onClick = { onSimilarSourceChange("description") },
                label = { Text("Semantic", style = MaterialTheme.typography.labelSmall) },
                colors = FilterChipDefaults.filterChipColors(labelColor = Color.White, selectedLabelColor = Color.Black)
            )
        }

        when {
            isLoadingSimilar -> LazyRow(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                item { CircularProgressIndicator(modifier = Modifier.size(24.dp)) }
            }
            similarMemes.isNotEmpty() -> LazyRow(
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(4.dp)
            ) {
                items(similarMemes, key = { it.id }) { similar ->
                    AsyncImage(
                        model = "http://localhost${similar.imageUrl}",
                        contentDescription = similar.originalFileName,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier
                            .size(80.dp)
                            .clip(RoundedCornerShape(4.dp))
                            .clickable { onSimilarMemeClick(similar.id, similarSource) }
                    )
                }
            }
            else -> Text(
                text = if (similarSource == "description") "No semantic similarity available for this image yet" else "No similar images found",
                style = MaterialTheme.typography.bodySmall,
                color = Color.White.copy(alpha = 0.6f),
                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
            )
        }
```

- [ ] **Step 2: Update `MemeDetailScreen`'s own signature and call site**

Change `MemeDetailScreen`'s signature (currently lines 64-69):

```kotlin
fun MemeDetailScreen(
    memeId: String,
    onBack: () -> Unit,
    onNavigateToMeme: (String) -> Unit,
    onTagClick: (category: String, value: String) -> Unit = { _, _ -> },
    viewModel: MemeDetailViewModel = hiltViewModel()
) {
```

to:

```kotlin
fun MemeDetailScreen(
    memeId: String,
    onBack: () -> Unit,
    onNavigateToMeme: (id: String, source: String) -> Unit,
    onTagClick: (category: String, value: String) -> Unit = { _, _ -> },
    viewModel: MemeDetailViewModel = hiltViewModel()
) {
```

Update the `BottomActionBar` call site (currently lines 129-140):

```kotlin
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
```

to:

```kotlin
                BottomActionBar(
                    meme = meme,
                    isSaving = state.isSaving,
                    onSave = { viewModel.saveToGallery(context) },
                    onShare = { viewModel.share(context) },
                    onToggleFlagged = { viewModel.toggleFlagged() },
                    similarMemes = state.similarMemes,
                    isLoadingSimilar = state.isLoadingSimilar,
                    similarSource = state.similarSource,
                    onSimilarSourceChange = { viewModel.setSimilarSource(it) },
                    onSimilarMemeClick = onNavigateToMeme,
                    onTagClick = onTagClick,
                    onInfoClick = { showDescriptions = true }
                )
```

(`onSimilarMemeClick = onNavigateToMeme` still works as a direct passthrough — both now share the `(id: String, source: String) -> Unit` shape.)

- [ ] **Step 3: Wire the navigation argument in `NavGraph.kt`**

In `AndroidClient/app/src/main/java/com/memebrowser/app/ui/NavGraph.kt`, change the `"detail/{memeId}"` composable (currently lines 36-55):

```kotlin
        composable(
            route = "detail/{memeId}",
            arguments = listOf(navArgument("memeId") { type = NavType.StringType })
        ) { backStack ->
            val memeId = backStack.arguments!!.getString("memeId")!!
            MemeDetailScreen(
                memeId = memeId,
                onBack = { navController.popBackStack() },
                onNavigateToMeme = { id ->
                    navController.navigate("detail/$id") {
                        popUpTo("detail/{memeId}") { inclusive = true }
                    }
                },
                onTagClick = { category, value ->
                    navController.getBackStackEntry("search")
                        .savedStateHandle["pending_tag"] = "$category:$value"
                    navController.popBackStack()
                }
            )
        }
```

to:

```kotlin
        composable(
            route = "detail/{memeId}?source={source}",
            arguments = listOf(
                navArgument("memeId") { type = NavType.StringType },
                navArgument("source") { type = NavType.StringType; defaultValue = "image" }
            )
        ) { backStack ->
            val memeId = backStack.arguments!!.getString("memeId")!!
            MemeDetailScreen(
                memeId = memeId,
                onBack = { navController.popBackStack() },
                onNavigateToMeme = { id, source ->
                    navController.navigate("detail/$id?source=$source") {
                        popUpTo("detail/{memeId}?source={source}") { inclusive = true }
                    }
                },
                onTagClick = { category, value ->
                    navController.getBackStackEntry("search")
                        .savedStateHandle["pending_tag"] = "$category:$value"
                    navController.popBackStack()
                }
            )
        }
```

The other three `navController.navigate("detail/$memeId")` call sites (in the `"search"`, `"flagged"`, and `"recommendations?q={query}"` composables) are unchanged — they have no prior similarity selection to carry, so they keep navigating to the bare route and pick up the `source` argument's `"image"` default.

- [ ] **Step 4: Fix the instrumented UI test's now-broken signatures**

`AndroidClient/app/src/androidTest/java/com/memebrowser/app/ui/detail/MemeDetailScreenTest.kt` calls `MemeDetailScreen` with the old `onNavigateToMeme` shape. This file lives in the `androidTest` source set, which `:app:testDebugUnitTest` does **not** compile — so Step 6's test run won't catch a break here, but a real device/CI build (`:app:assembleDebug` or `:app:connectedDebugAndroidTest`) would fail. Fix it now:

Change the stub in `setup()` (line 39):

```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.success(emptyList())
```

to:

```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(emptyList())
```

Change `setContent`'s parameter (line 49):

```kotlin
    private fun setContent(
        onBack: () -> Unit = {},
        onNavigateToMeme: (String) -> Unit = {}
    ) {
```

to:

```kotlin
    private fun setContent(
        onBack: () -> Unit = {},
        onNavigateToMeme: (id: String, source: String) -> Unit = { _, _ -> }
    ) {
```

Change the stub in `similarThumbnail_click_invokesNavigateCallback` (line 107 and 110):

```kotlin
        coEvery { repo.getSimilarMemes("meme-1") } returns Result.success(listOf(similarMeme))
        viewModel = MemeDetailViewModel(SavedStateHandle(mapOf("memeId" to "meme-1")), repo, envRepo)
        var navigatedTo: String? = null
        setContent(onNavigateToMeme = { navigatedTo = it })
```

to:

```kotlin
        coEvery { repo.getSimilarMemes("meme-1", "image") } returns Result.success(listOf(similarMeme))
        viewModel = MemeDetailViewModel(SavedStateHandle(mapOf("memeId" to "meme-1")), repo, envRepo)
        var navigatedTo: String? = null
        setContent(onNavigateToMeme = { id, _ -> navigatedTo = id })
```

- [ ] **Step 5: Compile check — both the app and the instrumented test source set**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:compileDebugKotlin --no-daemon
.\gradlew.bat :app:compileDebugAndroidTestKotlin --no-daemon
```

Expected: both BUILD SUCCESSFUL. The second command is what actually verifies Step 4's fix — `testDebugUnitTest` alone would not catch a broken `androidTest` source set.

- [ ] **Step 6: Full Android pre-commit gate**

```powershell
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:testDebugUnitTest --no-daemon
```

Expected: BUILD SUCCESSFUL, all unit tests pass (including Task 3's new tests).

- [ ] **Step 7: Manual verification (requires a connected device/emulator — not runnable in this sandboxed environment; do not skip silently, note if it can't be run here)**

Install and open the app, navigate to an image detail screen, confirm:
- "Visual" and "Semantic" chips appear above the similar-images strip, Visual selected by default.
- Tapping "Semantic" on an image with a description embedding shows semantically-similar images; on an image without one, shows "No semantic similarity available for this image yet".
- Tapping "Visual" switches back and refetches CLIP-based results.
- With Semantic selected, tapping a similar-image thumbnail opens that image's detail screen with Semantic already selected (carry-over via the nav argument).

- [ ] **Step 8: Commit**

```bash
git add AndroidClient/app/src/main/java/com/memebrowser/app/ui/detail/MemeDetailScreen.kt \
  AndroidClient/app/src/main/java/com/memebrowser/app/ui/NavGraph.kt \
  AndroidClient/app/src/androidTest/java/com/memebrowser/app/ui/detail/MemeDetailScreenTest.kt
git commit -m "feat: add similarity toggle UI and nav carry-over on Android"
```

---

## Final verification (after all tasks)

```bash
# Frontend (from Frontend/memes-frontend/)
tsc -b
eslint src/
vitest run
```

```powershell
# Android
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
.\gradlew.bat :app:testDebugUnitTest --no-daemon
```

Then, per this repo's pre-commit checklist: manually confirm both platforms' toggles work against a real `general`-environment image that has a description embedding (run `batch/build_image_description_embeddings.py` first if none exist) and one that doesn't, to exercise both the populated and empty-state paths on the Semantic tab.
