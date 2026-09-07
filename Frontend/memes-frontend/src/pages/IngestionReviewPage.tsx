import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import type { MemesApi, IngestionTier } from "../api/MemesApi"
import type {
  IngestionCluster, IngestionClusterMember, IngestionResolveResponse, IngestionRunStatus,
} from "../types/generated/all"
import { Modal } from "../components/Modal"
import { ClusterRow } from "../components/ingestion/ClusterRow"
import { DockedPreview } from "../components/ingestion/DockedPreview"
import type { Decision } from "../components/ingestion/types"

type Props = { memesApi: MemesApi }

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

// The message shown after a submit whose response includes failed/move_failed entries. Built
// from only the non-empty parts so a pure move-failure batch doesn't read "0 decision(s)
// failed to apply" -- see docs/superpowers/specs/2026-08-16-ingestion-resolve-atomicity-design.md.
function formatResolveSummary(response: IngestionResolveResponse): string | null {
  const parts: string[] = []
  if (response.failed.length > 0) {
    parts.push(`${response.failed.length} decision(s) failed to apply and remain marked for retry`)
  }
  if (response.move_failed.length > 0) {
    parts.push(
      `${response.move_failed.length} were recorded as rejected even though their file move failed` +
      ` (safe -- no data was lost; reversing it requires the backend's undo-reject API, not available in this page yet)`
    )
  }
  return parts.length > 0 ? parts.join("; ") : null
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

function Shell({ children }: { children: React.ReactNode }) {
  return <div><h1 className="text-2xl font-bold mb-4">Ingestion Review</h1>{children}</div>
}

const EMPTY_PAGE = { items: [] as IngestionCluster[], next_cursor: null as string | null, has_next: false }

export default function IngestionReviewPage({ memesApi }: Props) {
  const [status, setStatus] = useState<IngestionRunStatus | null>(null)
  const [clusters, setClusters] = useState<IngestionCluster[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasNext, setHasNext] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [decisions, setDecisions] = useState<Record<string, Decision | undefined>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // Keyed by the cluster object, never a list index -- optimistic removal reshuffles indices
  // mid-request, so an index key would move "Submitting…" onto whatever cluster slid into that slot.
  const [submitting, setSubmitting] = useState<IngestionCluster | "all" | null>(null)
  const [preview, setPreview] = useState<IngestionClusterMember | null>(null)
  const [peek, setPeek] = useState<IngestionClusterMember | null>(null)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const confirmAllTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const previewCloseRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const preloadedRef = useRef<Set<string>>(new Set())
  const loadingMoreRef = useRef(false)

  const tier = status ? tierForStage(status.stage) : null

  const load = useCallback(() => {
    setLoading(true)
    return memesApi.getIngestionRunStatus()
      .then((s) => {
        setStatus(s)
        setError(null)
        const t = s ? tierForStage(s.stage) : null
        return t ? memesApi.getIngestionClusters(t, undefined) : EMPTY_PAGE
      })
      .then((pageResult) => {
        setClusters(pageResult.items)
        setNextCursor(pageResult.next_cursor)
        setHasNext(pageResult.has_next)
        // A server reload is authoritative: drop any local decision whose target is no longer a
        // pending member of the fresh queue (covers a decision resolve() silently skipped -- one
        // that came back in none of rejected/kept/failed/move_failed).
        const pendingIds = new Set(
          pageResult.items.flatMap((c) => c.members.filter((m) => m.status === "pending").map((m) => m.image_id))
        )
        setDecisions((prev) => {
          const next: Record<string, Decision | undefined> = {}
          for (const [id, d] of Object.entries(prev)) if (pendingIds.has(id)) next[id] = d
          return next
        })
      })
      .catch((e: unknown) => {
        setStatus(null); setClusters([]); setNextCursor(null); setHasNext(false)
        setError(e instanceof Error ? e.message : "Failed to load ingestion review")
      })
      .finally(() => setLoading(false))
  }, [memesApi])

  const loadedRef = useRef(false)
  useEffect(() => { if (loadedRef.current) return; loadedRef.current = true; void load() }, [load])

  useEffect(() => () => {
    if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
    if (previewCloseRef.current) clearTimeout(previewCloseRef.current)
  }, [])

  // A tier change means the review queue was rebuilt server-side -- start local decisions fresh
  // (see docs/superpowers/specs/2026-08-16-ingestion-decision-staleness-guard-design.md). Guarded
  // on `tier` being truthy so a transient load failure (which nulls status/tier) doesn't wipe
  // un-submitted decisions on a Retry. `await Promise.resolve()` defers the setState past the
  // synchronous effect body, per react-hooks/set-state-in-effect (same pattern as AdminBatchesPage).
  useEffect(() => {
    if (!tier) return
    void (async () => { await Promise.resolve(); setDecisions({}) })()
  }, [tier])

  const loadMore = useCallback(async () => {
    // Ref latch, checked before the state guard: a Virtuoso `endReached` and a "Load more" click
    // can both fire in the same tick, before `loadingMore` has re-rendered.
    if (loadingMoreRef.current || loadingMore || !hasNext || !nextCursor || !tier) return
    loadingMoreRef.current = true
    setLoadingMore(true)
    try {
      const pageResult = await memesApi.getIngestionClusters(tier, nextCursor)
      setClusters((prev) => [...prev, ...pageResult.items])
      setNextCursor(pageResult.next_cursor)
      setHasNext(pageResult.has_next)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load more clusters")
    } finally {
      loadingMoreRef.current = false
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

  async function runSubmit(which: IngestionCluster | "all", toSubmit: { cluster: IngestionCluster; index: number }[]) {
    if (!tier) return
    const payload = toSubmit.flatMap(({ cluster }) => decidedPendingIn(cluster))
    if (payload.length === 0) return
    // Clusters we optimistically pull out of the list now, with their original position for rollback.
    const removed = toSubmit.filter(({ cluster }) => isFullyResolved(cluster))
    const removedSet = new Set(removed.map(({ cluster }) => cluster))
    setSubmitting(which)
    // The docked preview is a transient hover aid -- drop it on submit so it can't linger
    // pointing at a member of a cluster we're about to pull out of the list.
    setPreview(null)
    if (previewCloseRef.current) clearTimeout(previewCloseRef.current)
    // Compute the post-removal visible count inside the updater -- reading it back from a ref
    // after the await races the passive effect that would sync the ref.
    let survivingCount = 0
    setClusters((prev) => {
      const next = prev.filter((c) => !removedSet.has(c))
      survivingCount = next.length
      return next
    })
    try {
      const response: IngestionResolveResponse = await memesApi.resolveIngestionCluster(tier, payload)
      const failedIds = new Set(response.failed.map((f) => f.image_id))
      // Payload-scoped, response-time prune: clear every id in THIS submit's payload except the
      // ones the server reports as `failed` (those stay selected for retry). Race-free -- it
      // never touches a failed id, so it can't fight the re-insert below -- and strictly stronger
      // than the old always-on [clusters] effect: it also clears a decision the backend silently
      // skipped (id in none of rejected/kept/failed/move_failed). move_failed is a subset of
      // rejected, so no special case.
      setDecisions((prev) => {
        const next = { ...prev }
        for (const { image_id } of payload) if (!failedIds.has(image_id)) delete next[image_id]
        return next
      })
      const reinsert = failedIds.size > 0
        ? removed
            .filter(({ cluster }) => cluster.members.some((m) => failedIds.has(m.image_id)))
            .slice()
            .sort((a, b) => a.index - b.index)
        : []
      // Members the server actually resolved (not failed) that are still sitting in a *surviving*
      // cluster (a partially-resolved one `isFullyResolved` kept): flip their local status to
      // "active" so MemberTile stops offering Keep/Reject -- otherwise, with the decision
      // highlight just pruned, the tile reads as "my submit didn't take".
      const resolvedIds = new Set(
        [...response.rejected, ...response.kept].filter((id) => !failedIds.has(id))
      )
      if (reinsert.length > 0 || resolvedIds.size > 0) {
        setClusters((prev) => {
          let copy = [...prev]
          for (const { cluster, index } of reinsert) copy.splice(Math.min(index, copy.length), 0, cluster)
          if (resolvedIds.size > 0) {
            copy = copy.map((c) =>
              c.members.some((m) => m.status === "pending" && resolvedIds.has(m.image_id))
                ? {
                    ...c,
                    members: c.members.map((m) =>
                      m.status === "pending" && resolvedIds.has(m.image_id) ? { ...m, status: "active" } : m
                    ),
                  }
                : c
            )
          }
          return copy
        })
      }
      // Ruling 3: reload only when the on-screen queue has fully emptied -- restores the old
      // auto-advance (Tier A -> Tier B, via load() also refetching run status) and, when more
      // pages exist, pulls the next unreviewed page-1 work in place of a bare "Load more" button.
      // Every submit that leaves clusters visible stays purely optimistic (no reload/scroll jump).
      const remaining = survivingCount + reinsert.length
      if (remaining === 0) {
        await load()
      }
      // Set the summary after any reload -- load()'s success path clears `error`, so setting it
      // first would have the reload immediately wipe a move-failed / partial-failure summary.
      setError(formatResolveSummary(response))
    } catch (e: unknown) {
      // Roll the optimistically-removed clusters back into place. Decisions were never touched
      // on the way out, so they're still selected -- nothing to restore there.
      setClusters((prev) => {
        const copy = [...prev]
        for (const { cluster, index } of removed.slice().sort((a, b) => a.index - b.index)) {
          copy.splice(Math.min(index, copy.length), 0, cluster)
        }
        return copy
      })
      setError(e instanceof Error ? e.message : "Failed to submit decisions")
    } finally {
      setSubmitting(null)
    }
  }

  const submitCluster = (cluster: IngestionCluster, index: number) => runSubmit(cluster, [{ cluster, index }])
  const submitAll = () => runSubmit("all", clusters.map((cluster, index) => ({ cluster, index })))

  function handleSubmitAllClick() {
    if (confirmingAll) {
      if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
      setConfirmingAll(false)
      void submitAll()
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
  if (error && !status) return (
    <Shell>
      <p className="text-sm text-red-500 mb-3">{error}</p>
      <button className="text-sm rounded bg-blue-600 text-white px-3 py-1" onClick={() => void load()}>Retry</button>
    </Shell>
  )
  if (!status) return <Shell><p className="text-sm text-gray-400">No ingestion run is currently in progress.</p></Shell>

  const previewDecision = preview ? decisions[preview.image_id] : undefined

  return (
    // Permanent lg gutter for the desktop-only docked pane -- toggling it with `preview` made
    // the list (and <Virtuoso useWindowScroll>) reflow/re-measure on every hover.
    <div className="lg:pr-[42vw]">
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
              onSubmit={() => void submitCluster(cluster, index)}
              submitting={submitting === cluster || submitting === "all"}
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
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 rounded-full bg-white shadow-2xl border px-5 py-2">
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
        onDecide={(d) => { if (preview) setDecision(preview.image_id, d) }}
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
