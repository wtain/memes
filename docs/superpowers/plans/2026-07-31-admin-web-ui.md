# Admin Batch Controller Web UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/admin` page to the React frontend that triggers `trends_batch`, `move_flagged`,
and `unregister_deleted_images` on demand and shows run status/history, backed by the already-merged
`Backend/app/api/admin.py` endpoints.

**Architecture:** Three new JSON schemas in `shared/schemas/` (regenerated into TS/Kotlin types)
back two new `MemesApi` methods (`triggerBatchRun`, `listBatchRuns`) implemented in `HttpMemesApi`.
A single new page component, `AdminBatchesPage.tsx`, consumes them: a trigger toolbar (one row per
hardcoded batch name, two-click confirm) plus a paginated, auto-polling run-history table.

**Tech Stack:** React, TypeScript, react-router-dom, Tailwind CSS, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-07-31-admin-web-ui-design.md`

## Global Constraints

- No authentication/authorization — matches the backend's deliberate no-auth-yet posture.
- The three batch names (`trends_batch`, `move_flagged`, `unregister_deleted_images`) are hardcoded
  in the page — sourced from `environments/batch_registry.yaml`, not discovered dynamically (no
  such endpoint exists).
- `PAGE_SIZE = 20`, poll interval `4000`ms (active only while the currently-loaded page has a
  `"running"` row), confirm-button revert timeout `3000`ms — exact values, not tunable via props.
- No log-viewing-by-run_id in this UI — an operator still reads `logs/{env}/` directly on the box.
- Regenerate both `Frontend/memes-frontend/src/types/generated/all.d.ts` and
  `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` after adding the
  shared schemas (project convention — all API types come from `shared/schemas/`), even though
  Android has no use for the new types.

---

### Task 1: Shared JSON schemas for the admin response types

**Files:**
- Create: `shared/schemas/runtriggerresponse.schema.json`
- Create: `shared/schemas/runstatusresponse.schema.json`
- Create: `shared/schemas/runlistresponse.schema.json`
- Modify: `shared/schemas/all.schema.json`
- Modify (generated): `Frontend/memes-frontend/src/types/generated/all.d.ts`
- Modify (generated): `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt`

**Interfaces:**
- Produces: TypeScript interfaces `RunTriggerResponse { run_id: string; status: string }`,
  `RunStatusResponse { run_id: string; batch_name: string; trigger: string; status: string;
  created_at: string; completed_at: string | null; error: string | null }`, `RunListResponse {
  items: RunStatusResponse[]; total: number }`. Task 2 and Task 3 both import these from
  `../types/generated/all`.

No code depends on anything from this task except the generated types themselves — this task has
no unit tests of its own; verification is generation succeeding and the types matching by hand.

- [ ] **Step 1: Create the three schema files**

`shared/schemas/runtriggerresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "runtriggerresponse.schema.json",
  "title": "RunTriggerResponse",
  "type": "object",
  "properties": {
    "run_id": { "type": "string" },
    "status": { "type": "string", "description": "running" }
  },
  "required": ["run_id", "status"]
}
```

`shared/schemas/runstatusresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "runstatusresponse.schema.json",
  "title": "RunStatusResponse",
  "type": "object",
  "properties": {
    "run_id": { "type": "string" },
    "batch_name": { "type": "string" },
    "trigger": { "type": "string", "description": "manual | scheduled | unknown" },
    "status": { "type": "string", "description": "running | completed | failed" },
    "created_at": { "type": "string", "format": "date-time" },
    "completed_at": { "type": ["string", "null"], "format": "date-time" },
    "error": { "type": ["string", "null"] }
  },
  "required": ["run_id", "batch_name", "trigger", "status", "created_at", "completed_at", "error"]
}
```

`shared/schemas/runlistresponse.schema.json`:

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "$id": "runlistresponse.schema.json",
  "title": "RunListResponse",
  "type": "object",
  "properties": {
    "items": { "type": "array", "items": { "$ref": "./runstatusresponse.schema.json" } },
    "total": { "type": "integer" }
  },
  "required": ["items", "total"]
}
```

- [ ] **Step 2: Register the three schemas in `all.schema.json`**

Add three entries to the `definitions` object in `shared/schemas/all.schema.json`, alongside the
existing `Ingestion*` entries:

```json
    "RunTriggerResponse":      { "$ref": "runtriggerresponse.schema.json" },
    "RunStatusResponse":       { "$ref": "runstatusresponse.schema.json" },
    "RunListResponse":         { "$ref": "runlistresponse.schema.json" }
```

- [ ] **Step 3: Regenerate frontend types**

Run from `Frontend/`:

```bash
bash generate-types.sh
```

Verify `Frontend/memes-frontend/src/types/generated/all.d.ts` now contains `RunTriggerResponse`,
`RunStatusResponse`, and `RunListResponse` interfaces matching the field names/types above. Run
`git diff Frontend/memes-frontend/src/types/generated/all.d.ts` and confirm the only changes are
additive (the three new interfaces) — no unrelated churn.

- [ ] **Step 4: Regenerate Android DTOs**

Run from the repo root:

```bash
python AndroidClient/scripts/generate_dtos.py
```

Verify `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` now contains
`RunTriggerResponse`, `RunStatusResponse`, and `RunListResponse` data classes. `git diff` it and
confirm the change is additive only.

- [ ] **Step 5: Commit**

```bash
git add shared/schemas/runtriggerresponse.schema.json shared/schemas/runstatusresponse.schema.json shared/schemas/runlistresponse.schema.json shared/schemas/all.schema.json Frontend/memes-frontend/src/types/generated/all.d.ts AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt
git commit -m "feat: add shared schemas for admin batch run response types"
```

---

### Task 2: `MemesApi` methods for triggering and listing batch runs

**Files:**
- Modify: `Frontend/memes-frontend/src/api/MemesApi.ts`
- Modify: `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`
- Modify: `Frontend/memes-frontend/src/test/mockApi.ts`

**Interfaces:**
- Consumes: `RunTriggerResponse`, `RunListResponse` (Task 1).
- Produces: `MemesApi.triggerBatchRun(batchName: string): Promise<RunTriggerResponse>`,
  `MemesApi.listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse>`. Task 3's
  page and tests call these by these exact names/signatures, and `makeMockApi`'s defaults must
  satisfy them.

This task has no dedicated test file — matching the existing convention that `HttpMemesApi`'s
other methods (e.g. `getIngestionRunStatus`, `resolveIngestionCluster`) have no direct unit tests
of their own; they're exercised indirectly through page-level tests against the mocked `MemesApi`
interface in Task 3. `triggerBatchRun` builds its own human-readable error messages for the two
status codes the page needs to distinguish (`409`, `404`), following the same pattern
`getIngestionRunStatus` already uses for special-casing `404` — this keeps status-code handling in
one place (the HTTP client) rather than splitting it between the client and the page.

- [ ] **Step 1: Add the two methods to the `MemesApi` interface**

In `Frontend/memes-frontend/src/api/MemesApi.ts`, add to the type-only import at the top:

```typescript
import type {
  Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse,
  TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse,
  IngestionRunStatus, IngestionPendingImage, IngestionCluster, IngestionDecision,
  IngestionResolveResponse, IngestionUndoRejectResponse,
  RunTriggerResponse, RunListResponse,
} from "../types/generated/all";
```

And add to the `MemesApi` interface body, after `undoIngestionReject`:

```typescript
  triggerBatchRun(batchName: string): Promise<RunTriggerResponse>;
  listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse>;
```

- [ ] **Step 2: Implement both methods in `HttpMemesApi`**

In `Frontend/memes-frontend/src/api/http/HttpMemesApi.ts`, add the same two names to its import
from `../../types/generated/all`, and add these methods after `undoIngestionReject` (before the
closing `}` of the class):

```typescript
  async triggerBatchRun(batchName: string): Promise<RunTriggerResponse> {
    const res = await fetch(`${this.baseUrl}/api/admin/batches/${batchName}/run`, {
      method: "POST",
      headers: { Accept: "application/json" },
    })
    if (res.status === 409) throw new Error(`${batchName} is already running`)
    if (res.status === 404) throw new Error(`${batchName} is not a recognized batch`)
    if (!res.ok) throw new Error(`Failed to trigger ${batchName}: ${res.status}`)
    return res.json()
  }

  async listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse> {
    const params = new URLSearchParams()
    if (limit !== undefined) params.set("limit", String(limit))
    if (offset !== undefined) params.set("offset", String(offset))
    const qs = params.toString()
    const res = await fetch(`${this.baseUrl}/api/admin/batches/runs${qs ? `?${qs}` : ""}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch batch runs: ${res.status}`)
    return res.json()
  }
```

- [ ] **Step 3: Add defaults to `makeMockApi`**

In `Frontend/memes-frontend/src/test/mockApi.ts`, add to the returned object, after
`undoIngestionReject`:

```typescript
    triggerBatchRun: vi.fn().mockResolvedValue({ run_id: 'run-1', status: 'running' }),
    listBatchRuns: vi.fn().mockResolvedValue({ items: [], total: 0 }),
```

- [ ] **Step 4: Type-check**

Run from `Frontend/memes-frontend/`:

```bash
tsc -b
```

Expected: no errors. (No behavior to test yet — Task 3 is where these methods get exercised.)

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/api/MemesApi.ts Frontend/memes-frontend/src/api/http/HttpMemesApi.ts Frontend/memes-frontend/src/test/mockApi.ts
git commit -m "feat: add triggerBatchRun and listBatchRuns to MemesApi"
```

---

### Task 3: `AdminBatchesPage` component

**Files:**
- Create: `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`
- Test: `Frontend/memes-frontend/src/pages/AdminBatchesPage.test.tsx`

**Interfaces:**
- Consumes: `MemesApi.triggerBatchRun`/`.listBatchRuns` (Task 2), `RunStatusResponse` (Task 1).
- Produces: `export default function AdminBatchesPage({ memesApi }: { memesApi: MemesApi })`. Task 4
  imports this exact default export to wire into the router.

- [ ] **Step 1: Write the failing test file**

Create `Frontend/memes-frontend/src/pages/AdminBatchesPage.test.tsx`:

```tsx
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AdminBatchesPage from './AdminBatchesPage'
import { makeMockApi } from '../test/mockApi'
import type { RunStatusResponse, RunListResponse } from '../types/generated/all'

function makeRun(overrides: Partial<RunStatusResponse> = {}): RunStatusResponse {
  return {
    run_id: 'run-1',
    batch_name: 'trends_batch',
    trigger: 'manual',
    status: 'completed',
    created_at: '2026-07-31T00:00:00Z',
    completed_at: '2026-07-31T00:01:00Z',
    error: null,
    ...overrides,
  }
}

function makeList(items: RunStatusResponse[], total = items.length): RunListResponse {
  return { items, total }
}

describe('AdminBatchesPage', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders one row per admin batch and the run history table', async () => {
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([makeRun()])),
    })
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    expect(screen.getByText('move_flagged')).toBeInTheDocument()
    expect(screen.getByText('unregister_deleted_images')).toBeInTheDocument()
  })

  it('requires a second click to trigger a batch run', async () => {
    const trigger = vi.fn().mockResolvedValue({ run_id: 'run-2', status: 'running' })
    const listBatchRuns = vi.fn().mockResolvedValue(makeList([]))
    const api = makeMockApi({ listBatchRuns, triggerBatchRun: trigger })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0])
    expect(trigger).not.toHaveBeenCalled()
    expect(screen.getByText('Confirm?')).toBeInTheDocument()

    await user.click(screen.getByText('Confirm?'))
    await waitFor(() => expect(trigger).toHaveBeenCalledWith('trends_batch'))
    await waitFor(() => expect(listBatchRuns).toHaveBeenCalledTimes(2)) // initial load + reload after trigger
  })

  it('cancels the first batch confirm when a different batch is clicked', async () => {
    const trigger = vi.fn().mockResolvedValue({ run_id: 'run-2', status: 'running' })
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([])),
      triggerBatchRun: trigger,
    })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0]) // trends_batch -> Confirm?
    await user.click(runButtons[1]) // move_flagged -> Confirm?, cancels trends_batch's

    expect(screen.getAllByText('Run')).toHaveLength(2) // trends_batch reverted, unregister still "Run"
    expect(screen.getAllByText('Confirm?')).toHaveLength(1)

    await user.click(screen.getByText('Confirm?'))
    await waitFor(() => expect(trigger).toHaveBeenCalledWith('move_flagged'))
    expect(trigger).not.toHaveBeenCalledWith('trends_batch')
  })

  it('reverts to "Run" if not confirmed within the timeout', async () => {
    vi.useFakeTimers()
    const api = makeMockApi({ listBatchRuns: vi.fn().mockResolvedValue(makeList([])) })
    render(<AdminBatchesPage memesApi={api} />)

    await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // flush initial load
    fireEvent.click(screen.getAllByText('Run')[0])
    expect(screen.getByText('Confirm?')).toBeInTheDocument()

    await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
    expect(screen.queryByText('Confirm?')).not.toBeInTheDocument()
    expect(screen.getAllByText('Run')).toHaveLength(3)
  })

  it('shows an inline error for a 409 without affecting other rows', async () => {
    const trigger = vi.fn().mockRejectedValue(new Error('trends_batch is already running'))
    const api = makeMockApi({
      listBatchRuns: vi.fn().mockResolvedValue(makeList([])),
      triggerBatchRun: trigger,
    })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('trends_batch')).toBeInTheDocument())
    const runButtons = screen.getAllByText('Run')
    await user.click(runButtons[0])
    await user.click(screen.getByText('Confirm?'))

    await waitFor(() => expect(screen.getByText('trends_batch is already running')).toBeInTheDocument())
    expect(screen.getAllByText('Run')).toHaveLength(3) // move_flagged/unregister rows unaffected
  })

  it('polls the run list while a run is active, and stops once nothing is running', async () => {
    vi.useFakeTimers()
    const listBatchRuns = vi.fn()
      .mockResolvedValueOnce(makeList([makeRun({ status: 'running' })]))
      .mockResolvedValueOnce(makeList([makeRun({ status: 'completed' })]))
    const api = makeMockApi({ listBatchRuns })
    render(<AdminBatchesPage memesApi={api} />)

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(listBatchRuns).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(listBatchRuns).toHaveBeenCalledTimes(2)

    await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
    expect(listBatchRuns).toHaveBeenCalledTimes(2) // no more polling once status is completed
  })

  it('paginates using Prev/Next based on total', async () => {
    const listBatchRuns = vi.fn().mockResolvedValue(makeList([makeRun()], 25))
    const api = makeMockApi({ listBatchRuns })
    const user = userEvent.setup()
    render(<AdminBatchesPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('Page 1 of 2')).toBeInTheDocument())
    expect(screen.getByText('Prev')).toBeDisabled()

    await user.click(screen.getByText('Next'))
    await waitFor(() => expect(listBatchRuns).toHaveBeenCalledWith(20, 20))
    expect(screen.getByText('Next')).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run from `Frontend/memes-frontend/`:

```bash
vitest run src/pages/AdminBatchesPage.test.tsx
```

Expected: FAIL — `Failed to resolve import "./AdminBatchesPage"`.

- [ ] **Step 3: Implement `AdminBatchesPage.tsx`**

Create `Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx`:

```tsx
import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { RunStatusResponse } from "../types/generated/all"

type Props = { memesApi: MemesApi }

const ADMIN_BATCHES = ["trends_batch", "move_flagged", "unregister_deleted_images"] as const
// Source of truth: environments/batch_registry.yaml. No endpoint enumerates these --
// the backend deliberately keeps the trigger surface a fixed allow-list.

const PAGE_SIZE = 20
const POLL_INTERVAL_MS = 4000
const CONFIRM_TIMEOUT_MS = 3000

const STATUS_COLOR: Record<string, string> = {
  running: "bg-blue-100 text-blue-800",
  completed: "bg-green-100 text-green-800",
  failed: "bg-red-100 text-red-800",
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${STATUS_COLOR[status] ?? "bg-gray-100 text-gray-800"}`}>
      {status}
    </span>
  )
}

function truncate(text: string | null, max: number): string {
  if (!text) return ""
  return text.length > max ? `${text.slice(0, max)}…` : text
}

function BatchRow({
  name, latestRun, pendingConfirm, triggerError, onRunClick,
}: {
  name: string
  latestRun: RunStatusResponse | undefined
  pendingConfirm: boolean
  triggerError: string | undefined
  onRunClick: () => void
}) {
  return (
    <div className="flex items-center gap-3 py-2 border-b last:border-0">
      <span className="font-medium w-56">{name}</span>
      {latestRun ? <StatusBadge status={latestRun.status} /> : <span className="text-xs text-gray-400">no recent run</span>}
      <button
        className={`ml-auto text-xs rounded px-3 py-1 ${pendingConfirm ? "bg-amber-500 text-white" : "bg-blue-600 text-white"}`}
        onClick={onRunClick}
      >
        {pendingConfirm ? "Confirm?" : "Run"}
      </button>
      {triggerError && <span className="text-xs text-red-500 ml-2">{triggerError}</span>}
    </div>
  )
}

export default function AdminBatchesPage({ memesApi }: Props) {
  const [runs, setRuns] = useState<RunStatusResponse[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pendingConfirm, setPendingConfirm] = useState<string | null>(null)
  const [triggerErrors, setTriggerErrors] = useState<Record<string, string>>({})
  const confirmTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(() => {
    return memesApi.listBatchRuns(PAGE_SIZE, page * PAGE_SIZE)
      .then((res) => {
        setRuns(res.items)
        setTotal(res.total)
        setError(null)
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load batch runs")
      })
      .finally(() => setLoading(false))
  }, [memesApi, page])

  useEffect(() => {
    setLoading(true)
    load()
  }, [load])

  const hasRunningRun = runs.some((r) => r.status === "running")
  useEffect(() => {
    if (!hasRunningRun) return
    const id = setInterval(load, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [hasRunningRun, load])

  useEffect(() => {
    return () => {
      if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current)
    }
  }, [])

  function handleRunClick(batchName: string) {
    if (pendingConfirm === batchName) {
      if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current)
      setPendingConfirm(null)
      memesApi.triggerBatchRun(batchName)
        .then(() => {
          setTriggerErrors((prev) => ({ ...prev, [batchName]: "" }))
          load()
        })
        .catch((e: unknown) => {
          setTriggerErrors((prev) => ({
            ...prev,
            [batchName]: e instanceof Error ? e.message : `Failed to trigger ${batchName}`,
          }))
        })
      return
    }

    if (confirmTimeoutRef.current) clearTimeout(confirmTimeoutRef.current)
    setPendingConfirm(batchName)
    confirmTimeoutRef.current = setTimeout(() => setPendingConfirm(null), CONFIRM_TIMEOUT_MS)
  }

  function latestRunFor(batchName: string): RunStatusResponse | undefined {
    return runs.find((r) => r.batch_name === batchName)
  }

  if (loading) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>
      <p className="text-sm text-gray-400">Loading…</p>
    </div>
  )

  if (error) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>
      <p className="text-sm text-red-500">{error}</p>
    </div>
  )

  const maxPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1)

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Admin</h1>

      <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
        {ADMIN_BATCHES.map((name) => (
          <BatchRow
            key={name}
            name={name}
            latestRun={latestRunFor(name)}
            pendingConfirm={pendingConfirm === name}
            triggerError={triggerErrors[name] || undefined}
            onRunClick={() => handleRunClick(name)}
          />
        ))}
      </div>

      <div className="bg-white rounded-lg p-4 shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-gray-500 border-b">
              <th className="py-1">Batch</th>
              <th>Trigger</th>
              <th>Status</th>
              <th>Created</th>
              <th>Completed</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.run_id} className="border-b last:border-0">
                <td className="py-1">{run.batch_name}</td>
                <td>{run.trigger}</td>
                <td><StatusBadge status={run.status} /></td>
                <td>{run.created_at}</td>
                <td>{run.completed_at ?? "—"}</td>
                <td title={run.error ?? undefined}>{truncate(run.error, 40)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {runs.length === 0 && <p className="text-sm text-gray-400 mt-2">No runs yet.</p>}

        <div className="flex items-center gap-3 mt-3">
          <button
            className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Prev
          </button>
          <span className="text-xs text-gray-500">Page {page + 1} of {maxPage + 1}</span>
          <button
            className="text-xs rounded bg-gray-100 px-3 py-1 disabled:opacity-40"
            disabled={page >= maxPage}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run from `Frontend/memes-frontend/`:

```bash
vitest run src/pages/AdminBatchesPage.test.tsx
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/pages/AdminBatchesPage.tsx Frontend/memes-frontend/src/pages/AdminBatchesPage.test.tsx
git commit -m "feat: add AdminBatchesPage component"
```

---

### Task 4: Routing, navigation, and full verification

**Files:**
- Modify: `Frontend/memes-frontend/src/app/router.tsx`
- Modify: `Frontend/memes-frontend/src/app/AppLayout.tsx`

**Interfaces:**
- Consumes: `AdminBatchesPage` default export (Task 3).

- [ ] **Step 1: Register the route**

In `Frontend/memes-frontend/src/app/router.tsx`, add the import alongside the other page imports:

```typescript
import AdminBatchesPage from "../pages/AdminBatchesPage";
```

And add the route after the `/ingestion` entry:

```typescript
      { path: "/admin", element: <AdminBatchesPage memesApi={memesApi} /> },
```

- [ ] **Step 2: Add the nav link**

In `Frontend/memes-frontend/src/app/AppLayout.tsx`, add after the `/ingestion` `NavLink` (before its
closing `</div>`):

```tsx
          <NavLink
            to="/admin"
            className={({ isActive }) =>
              isActive ? "font-semibold text-blue-600" : "text-gray-600"
            }
          >
            Admin
          </NavLink>
```

- [ ] **Step 3: Run full frontend verification**

Run from `Frontend/memes-frontend/`:

```bash
tsc -b
eslint src/
vitest run
```

Expected: `tsc -b` no errors; `eslint src/` zero warnings; `vitest run` all tests pass (including
the new `AdminBatchesPage.test.tsx` and every existing test file — no regressions).

- [ ] **Step 4: Manual verification (if a real dev environment is reachable)**

1. Start one backend (e.g. metal, port 8081) and its frontend (`pnpm dev`, port 5173).
2. Navigate to `http://localhost:5173/admin`.
3. Confirm all three batch rows render with their most recent status (or "no recent run").
4. Click **Run** on `move_flagged` — confirm it becomes "Confirm?"; click again — confirm a new
   "running" row appears in the history table within ~4 seconds without a manual page refresh, and
   transitions to "completed" once the job finishes.
5. Click **Run** twice (both clicks, full confirm) on the same batch back-to-back — confirm the
   second attempt shows the "already running" inline error under that row.

If no real environment is reachable in the implementing environment, note exactly what was and
wasn't verified live in the final report — the same sandbox limitation already documented for the
backend spec's own manual-verification step.

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src/app/router.tsx Frontend/memes-frontend/src/app/AppLayout.tsx
git commit -m "feat: wire AdminBatchesPage into routing and navigation"
```

## Self-Review Notes

- **Spec coverage:** shared schemas + type regeneration (Task 1), API client methods with
  status-code-specific error messages (Task 2), the page itself — toolbar, confirm flow, polling,
  pagination, error handling — plus its full test suite (Task 3), routing/nav wiring plus the
  spec's manual-verification checklist (Task 4). Every section of the spec has a corresponding
  task.
- **Deliberate refinement from the spec's exact wording:** the spec says "the page differentiates
  on `res.status`" for the 409/404 messages; this plan instead builds those messages inside
  `HttpMemesApi.triggerBatchRun` (Task 2) and has the page just display `e.message`. Net observable
  behavior is identical, it matches the existing precedent of `getIngestionRunStatus` special-casing
  `404` in the HTTP client rather than the page, and it keeps all status-code interpretation in one
  place instead of splitting it across two files.
- **Type consistency:** `MemesApi.triggerBatchRun`/`.listBatchRuns` signatures (Task 2) match
  exactly what `AdminBatchesPage` (Task 3) calls and what `makeMockApi`'s defaults (Task 2) provide;
  `RunStatusResponse`'s field names (Task 1) match every place Task 3 reads a run's properties
  (`batch_name`, `trigger`, `status`, `created_at`, `completed_at`, `error`).
- **No dedicated `HttpMemesApi` test file** — matches the existing convention that none of its other
  methods have one either; `triggerBatchRun`/`listBatchRuns` are exercised through
  `AdminBatchesPage.test.tsx`'s mocked `MemesApi`, same as every other page in this codebase.
