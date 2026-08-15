# Ingestion Review — Submit All Decisions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Submit all decisions" button to the ingestion review page that submits every currently-decided member across all loaded clusters in one request, instead of requiring a separate click per cluster.

**Architecture:** Frontend-only change to `IngestionReviewPage.tsx`. No backend changes — `POST /api/ingestion/clusters/{tier}/resolve` already accepts an arbitrary list of `{image_id, decision}` pairs with no per-cluster scoping, so "submit all" just collects more pairs into one call to the same endpoint the existing per-cluster button already uses.

**Tech Stack:** React, TypeScript, Vitest + `@testing-library/react` + `@testing-library/user-event`.

## Global Constraints

- `tsc -b`, `eslint src/` (0 warnings), `vitest run` must all pass before any commit touching `Frontend/memes-frontend/`.
- Collection scope: only members with an actual decision (`Keep`/`Reject` clicked) are included; a cluster with zero decided members contributes nothing, a cluster with some decided and some undecided members contributes only the decided ones — exactly matching what that cluster's own "Submit decisions" button would submit today.
- Confirmation: two-click confirm matching `AdminBatchesPage`'s Run button exactly (`CONFIRM_TIMEOUT_MS = 3000` convention).
- While "submit all" is in flight, every per-cluster "Submit decisions" button is also disabled (and vice versa), to prevent a per-cluster submit racing an in-flight "submit all" over the same images.

---

### Task 1: "Submit all decisions" button

**Files:**
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx`
- Modify: `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx`

**Interfaces:**
- Consumes: `MemesApi.resolveIngestionCluster(tier: IngestionTier, decisions: IngestionDecision[]): Promise<IngestionResolveResponse>` (existing, unchanged — same method the per-cluster button already calls).
- No new exports; this is a self-contained page-level change.

- [ ] **Step 1: Write the failing tests**

Replace `Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx` in full:

```typescript
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import IngestionReviewPage from './IngestionReviewPage'
import { makeMockApi } from '../test/mockApi'
import type { IngestionCluster, IngestionRunStatus } from '../types/generated/all'

const mockStatus: IngestionRunStatus = {
  run_id: 'run-1',
  status: 'started',
  stage: 'tier_a_review',
  stats: { intake: 3, registered: 2 },
  created_at: '2026-07-25T00:00:00Z',
  completed_at: null,
}

const mockCluster: IngestionCluster = {
  members: [
    { image_id: 'pending-1', filename: 'new.jpg', status: 'pending', ocr_text: null },
    { image_id: 'active-1', filename: 'existing.jpg', status: 'active', ocr_text: 'existing meme text' },
  ],
  edges: [
    { image_id1: 'pending-1', image_id2: 'active-1', distance: 0.021, match_source: 'cross_corpus' },
  ],
}

const mockClusterTwo: IngestionCluster = {
  members: [
    { image_id: 'pending-2', filename: 'second.jpg', status: 'pending', ocr_text: null },
  ],
  edges: [],
}

const mockClusterThree: IngestionCluster = {
  members: [
    { image_id: 'pending-3', filename: 'third.jpg', status: 'pending', ocr_text: null },
  ],
  edges: [],
}

describe('IngestionReviewPage', () => {
  it('shows a message when there is no active ingestion run', async () => {
    const api = makeMockApi()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() =>
      expect(screen.getByText('No ingestion run is currently in progress.')).toBeInTheDocument()
    )
  })

  it('renders cluster members and the run stage once loaded', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
    expect(screen.getByText('existing.jpg')).toBeInTheDocument()
    expect(screen.getByText('tier_a_review')).toBeInTheDocument()
    // active member is read-only context -- no Keep/Reject buttons for it
    expect(screen.getAllByText('Keep')).toHaveLength(1)
  })

  it('submits a reject decision for the pending member and reloads', async () => {
    const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: [] })
    const getIngestionClusters = vi.fn().mockResolvedValue([mockCluster])
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters,
      resolveIngestionCluster: resolve,
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

    await user.click(screen.getByText('Reject'))
    await user.click(screen.getByText('Submit decisions'))

    await waitFor(() =>
      expect(resolve).toHaveBeenCalledWith('tier_a', [{ image_id: 'pending-1', decision: 'reject' }])
    )
    expect(getIngestionClusters).toHaveBeenCalledTimes(2) // initial load + reload after submit
  })

  it('disables submit until a decision is made', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('Submit decisions')).toBeInTheDocument())
    expect(screen.getByText('Submit decisions')).toBeDisabled()
  })

  it('switches to the Tier B queue and shows OCR text once the run reaches tier_b_review', async () => {
    const tierBCluster: IngestionCluster = {
      members: [
        { image_id: 'pending-2', filename: 'meme.jpg', status: 'pending', ocr_text: 'nice meme bro' },
        { image_id: 'active-2', filename: 'template.jpg', status: 'active', ocr_text: 'original template text' },
      ],
      edges: [
        { image_id1: 'pending-2', image_id2: 'active-2', distance: 0.12, match_source: 'cross_corpus' },
      ],
    }
    const getIngestionClusters = vi.fn().mockResolvedValue([tierBCluster])
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue({ ...mockStatus, stage: 'tier_b_review' }),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('Ingestion Review — Tier B')).toBeInTheDocument())
    expect(getIngestionClusters).toHaveBeenCalledWith('tier_b')
    expect(screen.getByText('“nice meme bro”')).toBeInTheDocument()
    expect(screen.getByText('“original template text”')).toBeInTheDocument()
  })

  it('opens a lightbox with the full image when a thumbnail is clicked, and closes it', async () => {
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
      getIngestionClusters: vi.fn().mockResolvedValue([mockCluster]),
    })
    const user = userEvent.setup()
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

    // Only the thumbnail exists before the lightbox is opened.
    expect(screen.getAllByAltText('new.jpg')).toHaveLength(1)

    await user.click(screen.getAllByAltText('new.jpg')[0])

    // Opening it adds a second image with the same alt text -- the enlarged one.
    const images = await waitFor(() => {
      const found = screen.getAllByAltText('new.jpg')
      expect(found).toHaveLength(2)
      return found
    })
    const enlargedImage = images[1]
    expect(enlargedImage).toHaveAttribute('src', api.getImageUrlById('pending-1'))

    await user.click(screen.getByText('✕'))

    await waitFor(() => expect(screen.getAllByAltText('new.jpg')).toHaveLength(1))
  })

  it('shows a waiting message during the OCR pre-pass stage, without fetching clusters', async () => {
    const getIngestionClusters = vi.fn()
    const api = makeMockApi({
      getIngestionRunStatus: vi.fn().mockResolvedValue({ ...mockStatus, stage: 'ocr_prepass' }),
      getIngestionClusters,
    })
    render(<IngestionReviewPage memesApi={api} />)

    await waitFor(() =>
      expect(screen.getByText(/OCR is running/)).toBeInTheDocument()
    )
    expect(getIngestionClusters).not.toHaveBeenCalled()
  })

  describe('submit all decisions', () => {
    it('does not show "Submit all decisions" until at least one decision is made', async () => {
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo]),
      })
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())
      expect(screen.queryByText(/Submit all decisions/)).not.toBeInTheDocument()
    })

    it('shows the correct cluster/image counts across clusters with mixed decided and undecided members', async () => {
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo]),
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

      // Decides pending-1 (in mockCluster) only -- mockClusterTwo's pending-2 stays undecided.
      await user.click(screen.getAllByText('Reject')[0])

      expect(screen.getByText('Submit all decisions (1 cluster, 1 image)')).toBeInTheDocument()
    })

    it('submits decisions for all clusters with a decision, after confirming, excluding undecided members and clusters entirely', async () => {
      const resolve = vi.fn().mockResolvedValue({ rejected: ['pending-1'], kept: ['pending-3'] })
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo, mockClusterThree]),
        resolveIngestionCluster: resolve,
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

      // Keep buttons render in cluster order: pending-1 (index 0), pending-2 (index 1),
      // pending-3 (index 2) -- same for Reject. Decide pending-1 (reject) and pending-3 (keep);
      // leave mockClusterTwo's pending-2 undecided.
      await user.click(screen.getAllByText('Reject')[0])
      await user.click(screen.getAllByText('Keep')[2])

      expect(screen.getByText('Submit all decisions (2 clusters, 2 images)')).toBeInTheDocument()

      await user.click(screen.getByText(/Submit all decisions/))
      await user.click(screen.getByText('Confirm?'))

      await waitFor(() => expect(resolve).toHaveBeenCalledTimes(1))
      const [calledTier, calledDecisions] = resolve.mock.calls[0]
      expect(calledTier).toBe('tier_a')
      expect(calledDecisions).toHaveLength(2) // excludes pending-2 (undecided) entirely
      expect(calledDecisions).toEqual(expect.arrayContaining([
        { image_id: 'pending-1', decision: 'reject' },
        { image_id: 'pending-3', decision: 'keep' },
      ]))
    })

    it('disables per-cluster submit buttons while "submit all" is in flight', async () => {
      const resolve = vi.fn().mockImplementation(() => new Promise(() => {})) // never resolves
      const api = makeMockApi({
        getIngestionRunStatus: vi.fn().mockResolvedValue(mockStatus),
        getIngestionClusters: vi.fn().mockResolvedValue([mockCluster, mockClusterTwo]),
        resolveIngestionCluster: resolve,
      })
      const user = userEvent.setup()
      render(<IngestionReviewPage memesApi={api} />)

      await waitFor(() => expect(screen.getByText('new.jpg')).toBeInTheDocument())

      await user.click(screen.getAllByText('Reject')[0]) // decide pending-1 -- mockCluster's own
      // "Submit decisions" button would otherwise now be enabled.

      await user.click(screen.getByText(/Submit all decisions/))
      await user.click(screen.getByText('Confirm?'))

      await waitFor(() => expect(screen.getAllByText('Submit decisions')[0]).toBeDisabled())
    })
  })
})
```

- [ ] **Step 2: Run to verify it fails**

```
vitest run src/pages/IngestionReviewPage.test.tsx
```
Expected: FAIL — the four new tests in the `'submit all decisions'` describe block fail (the button doesn't exist yet), while all pre-existing tests still pass.

- [ ] **Step 3: Implement the button**

Replace `Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx` in full:

```typescript
import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi, IngestionTier } from "../api/MemesApi"
import type { IngestionCluster, IngestionRunStatus } from "../types/generated/all"
import { Modal } from "../components/Modal"

type Props = { memesApi: MemesApi }

type Decision = "reject" | "keep"

// Which tier's queue to show, driven by the run's current stage. "promoted" falls back to
// tier_b's (empty, by then) queue rather than a dedicated "done" view -- once a run
// completes it drops out of getIngestionRunStatus() entirely (no longer the active run), so
// this branch is mostly a safety net, not the normal path to seeing a finished run.
function tierForStage(stage: string | null): IngestionTier | null {
  if (stage === "tier_a_review") return "tier_a"
  if (stage === "tier_b_review" || stage === "promoted") return "tier_b"
  return null // hash_dedup, or ocr_prepass (transient; OCR now runs before Tier A review,
  // not between the tiers -- see Decision #10 in
  // docs/superpowers/specs/2026-07-24-ingestion-pipeline-design.md)
}

const TIER_LABEL: Record<IngestionTier, string> = { tier_a: "Tier A", tier_b: "Tier B" }
const CONFIRM_ALL_TIMEOUT_MS = 3000

function StatusBanner({ status }: { status: IngestionRunStatus | null }) {
  if (!status) return null
  const stats = status.stats ?? {}
  return (
    <div className="bg-white rounded-lg p-4 shadow-sm mb-6">
      <div className="flex items-center gap-3">
        <span className="text-sm text-gray-500">Run</span>
        <span className="font-mono text-xs text-gray-700">{status.run_id}</span>
        <span className="text-sm text-gray-500 ml-4">Stage</span>
        <span className="font-semibold">{status.stage}</span>
      </div>
      <div className="mt-2 flex gap-4 text-sm text-gray-600">
        {Object.entries(stats).map(([key, value]) => (
          <span key={key}>{key}: <span className="font-semibold">{String(value)}</span></span>
        ))}
      </div>
    </div>
  )
}

function MemberTile({
  memesApi, memberId, filename, memberStatus, ocrText, edgeLabels, decision, onDecide, onOpenImage,
}: {
  memesApi: MemesApi
  memberId: string
  filename: string
  memberStatus: string
  ocrText: string | null
  edgeLabels: string[]
  decision: Decision | undefined
  onDecide: (decision: Decision) => void
  onOpenImage: () => void
}) {
  const isPending = memberStatus === "pending"
  return (
    <div className={`border rounded-lg p-2 w-48 ${decision === "reject" ? "opacity-40" : ""}`}>
      <img
        src={memesApi.getImageUrlById(memberId)}
        alt={filename}
        className="w-full h-32 object-cover rounded cursor-pointer"
        onClick={onOpenImage}
      />
      <div className="text-xs mt-1 truncate" title={filename}>{filename}</div>
      <div className="text-xs">
        <span className={isPending ? "text-blue-600" : "text-gray-400"}>{memberStatus}</span>
      </div>
      {ocrText && (
        <div className="text-[11px] text-gray-700 mt-1 line-clamp-3" title={ocrText}>
          “{ocrText}”
        </div>
      )}
      {edgeLabels.map((label) => (
        <div key={label} className="text-[11px] text-gray-500">{label}</div>
      ))}
      {isPending && (
        <div className="flex gap-1 mt-2">
          <button
            className={`flex-1 text-xs rounded px-1 py-1 ${decision === "keep" ? "bg-green-600 text-white" : "bg-gray-100"}`}
            onClick={() => onDecide("keep")}
          >
            Keep
          </button>
          <button
            className={`flex-1 text-xs rounded px-1 py-1 ${decision === "reject" ? "bg-red-600 text-white" : "bg-gray-100"}`}
            onClick={() => onDecide("reject")}
          >
            Reject
          </button>
        </div>
      )}
    </div>
  )
}

export default function IngestionReviewPage({ memesApi }: Props) {
  const [status, setStatus] = useState<IngestionRunStatus | null>(null)
  const [clusters, setClusters] = useState<IngestionCluster[]>([])
  const [decisions, setDecisions] = useState<Record<string, Decision | undefined>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState<number | "all" | null>(null)
  const [enlargedMember, setEnlargedMember] = useState<{ memberId: string; filename: string } | null>(null)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const confirmAllTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const tier = status ? tierForStage(status.stage) : null

  const load = useCallback(() => {
    return memesApi.getIngestionRunStatus()
      .then((s) => {
        setStatus(s)
        setError(null)
        const tier = s ? tierForStage(s.stage) : null
        return tier ? memesApi.getIngestionClusters(tier) : []
      })
      .then(setClusters)
      .catch((e: unknown) => {
        setStatus(null)
        setClusters([])
        setError(e instanceof Error ? e.message : "Failed to load ingestion review")
      })
      .finally(() => setLoading(false))
  }, [memesApi])

  const loadedRef = useRef(false)
  useEffect(() => {
    if (loadedRef.current) return
    loadedRef.current = true
    load()
  }, [load])

  useEffect(() => {
    return () => {
      if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
    }
  }, [])

  function setDecision(memberId: string, decision: Decision) {
    setDecisions((prev) => ({ ...prev, [memberId]: prev[memberId] === decision ? undefined : decision }))
  }

  async function submitCluster(clusterIndex: number, cluster: IngestionCluster) {
    const memberIds = new Set(cluster.members.map((m) => m.image_id))
    const clusterDecisions: { image_id: string; decision: Decision }[] = []
    for (const [image_id, decision] of Object.entries(decisions)) {
      if (memberIds.has(image_id) && decision !== undefined) {
        clusterDecisions.push({ image_id, decision })
      }
    }

    if (clusterDecisions.length === 0 || !tier) return

    setSubmitting(clusterIndex)
    try {
      await memesApi.resolveIngestionCluster(tier, clusterDecisions)
      load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to submit decisions")
    } finally {
      setSubmitting(null)
    }
  }

  const allPendingDecisions: { image_id: string; decision: Decision }[] = []
  let clustersWithPendingCount = 0
  for (const cluster of clusters) {
    const before = allPendingDecisions.length
    for (const member of cluster.members) {
      const decision = decisions[member.image_id]
      if (decision !== undefined) allPendingDecisions.push({ image_id: member.image_id, decision })
    }
    if (allPendingDecisions.length > before) clustersWithPendingCount++
  }

  async function submitAll() {
    if (!tier || allPendingDecisions.length === 0) return
    setSubmitting("all")
    try {
      await memesApi.resolveIngestionCluster(tier, allPendingDecisions)
      load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to submit all decisions")
    } finally {
      setSubmitting(null)
    }
  }

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

  if (loading) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-gray-400">Loading…</p>
    </div>
  )

  if (error) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-red-500">{error}</p>
    </div>
  )

  if (!status) return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>
      <p className="text-sm text-gray-400">No ingestion run is currently in progress.</p>
    </div>
  )

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">
        Ingestion Review{tier ? ` — ${TIER_LABEL[tier]}` : ""}
      </h1>
      <StatusBanner status={status} />

      {tier && clustersWithPendingCount > 0 && (
        <button
          className={`mb-4 text-sm rounded px-3 py-1 text-white disabled:opacity-40 ${confirmingAll ? "bg-amber-500" : "bg-blue-600"}`}
          disabled={submitting !== null}
          onClick={handleSubmitAllClick}
        >
          {submitting === "all"
            ? "Submitting…"
            : confirmingAll
              ? "Confirm?"
              : `Submit all decisions (${clustersWithPendingCount} cluster${clustersWithPendingCount === 1 ? "" : "s"}, ${allPendingDecisions.length} image${allPendingDecisions.length === 1 ? "" : "s"})`}
        </button>
      )}

      {!tier && (
        <p className="text-sm text-gray-400">
          {status.stage === "ocr_prepass"
            ? "OCR is running — Tier A review will be available once it finishes."
            : "Candidates haven't been computed for this run yet."}
        </p>
      )}

      {tier && clusters.length === 0 && (
        <p className="text-sm text-gray-400">No {TIER_LABEL[tier]} clusters need review right now.</p>
      )}

      <div className="space-y-4">
        {clusters.map((cluster, i) => {
          const memberIds = cluster.members.map((m) => m.image_id)
          const hasPendingDecision = memberIds.some((id) => decisions[id] !== undefined)

          return (
            <div key={i} className="bg-white rounded-lg p-4 shadow-sm">
              <div className="flex flex-wrap gap-3">
                {cluster.members.map((member) => {
                  const edgeLabels = cluster.edges
                    .filter((e) => e.image_id1 === member.image_id || e.image_id2 === member.image_id)
                    .map((e) => `${e.distance.toFixed(3)} (${e.match_source ?? "?"})`)
                  return (
                    <MemberTile
                      key={member.image_id}
                      memesApi={memesApi}
                      memberId={member.image_id}
                      filename={member.filename}
                      memberStatus={member.status}
                      ocrText={member.ocr_text}
                      edgeLabels={edgeLabels}
                      decision={decisions[member.image_id]}
                      onDecide={(d) => setDecision(member.image_id, d)}
                      onOpenImage={() => setEnlargedMember({ memberId: member.image_id, filename: member.filename })}
                    />
                  )
                })}
              </div>
              <button
                className="mt-3 text-sm rounded bg-blue-600 text-white px-3 py-1 disabled:opacity-40"
                disabled={!hasPendingDecision || submitting === i || submitting === "all"}
                onClick={() => submitCluster(i, cluster)}
              >
                {submitting === i ? "Submitting…" : "Submit decisions"}
              </button>
            </div>
          )
        })}
      </div>

      {enlargedMember && (
        <Modal onClose={() => setEnlargedMember(null)} title={enlargedMember.filename}>
          <img
            src={memesApi.getImageUrlById(enlargedMember.memberId)}
            alt={enlargedMember.filename}
            className="max-w-full max-h-[80vh] object-contain"
          />
        </Modal>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run to verify it passes**

```
vitest run src/pages/IngestionReviewPage.test.tsx
```
Expected: all PASS (10 tests: 6 pre-existing + 4 new).

- [ ] **Step 5: Typecheck and lint**

From `Frontend/memes-frontend/`:
```
tsc -b
eslint src/pages/IngestionReviewPage.tsx src/pages/IngestionReviewPage.test.tsx
```
Expected: both clean, 0 warnings.

- [ ] **Step 6: Full frontend check**

```
tsc -b
eslint src/
vitest run
```
Expected: all PASS, 0 eslint warnings, no regressions elsewhere.

- [ ] **Step 7: Commit**

```bash
git add Frontend/memes-frontend/src/pages/IngestionReviewPage.tsx Frontend/memes-frontend/src/pages/IngestionReviewPage.test.tsx
git commit -m "feat: add Submit all decisions button to ingestion review"
```
