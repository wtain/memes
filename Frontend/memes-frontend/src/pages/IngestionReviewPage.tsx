import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import type { MemesApi, IngestionTier } from "../api/MemesApi"
import type {
  IngestionCluster, IngestionClusterMember, IngestionResolveResponse, IngestionRunStatus,
  IngestionTierBReviewItem,
} from "../types/generated/all"
import { Modal } from "../components/Modal"
import { ClusterRow } from "../components/ingestion/ClusterRow"
import { TierBReviewCard } from "../components/ingestion/TierBReviewCard"
import type { Decision } from "../components/ingestion/types"

type Props = { memesApi: MemesApi }

// A review unit is either a Tier A near-duplicate cluster or a Tier B per-image review item.
// The submit machinery (optimistic removal, `submitting`/`expanded` keyed by object identity,
// the reload-when-empty gate) is entirely unit-object / image_id based -- only the two shape
// helpers below need to know which kind they're looking at.
type ReviewUnit = IngestionCluster | IngestionTierBReviewItem

const isTierBItem = (u: ReviewUnit): u is IngestionTierBReviewItem => "image" in u

// Every image id a unit can carry a decision for (subject + its pending members/candidates),
// used to decide whether a `failed` server response should re-insert an optimistically-removed unit.
function unitImageIds(u: ReviewUnit): string[] {
  return isTierBItem(u)
    ? [u.image.image_id, ...u.candidates.map((c) => c.member.image_id)]
    : u.members.map((m) => m.image_id)
}

// After a submit, flip the local status of members/candidates the server actually resolved (not
// `failed`) so their tiles stop offering Keep/Reject -- otherwise, with the decision highlight
// pruned, a still-pending-looking tile reads as "my submit didn't take". The status *label* stays
// truthful (a rejected id -> "rejected", a kept id -> "active") via the per-id `resolvedStatus`.
function flipResolved(unit: ReviewUnit, resolvedStatus: (id: string) => string | null): ReviewUnit {
  if (isTierBItem(unit)) {
    const imgNext = unit.image.status === "pending" ? resolvedStatus(unit.image.image_id) : null
    const candNeedsFlip = unit.candidates.some(
      (c) => c.member.status === "pending" && resolvedStatus(c.member.image_id) !== null
    )
    if (!imgNext && !candNeedsFlip) return unit
    return {
      ...unit,
      image: imgNext ? { ...unit.image, status: imgNext } : unit.image,
      candidates: unit.candidates.map((c) => {
        const next = c.member.status === "pending" ? resolvedStatus(c.member.image_id) : null
        return next ? { ...c, member: { ...c.member, status: next } } : c
      }),
    }
  }
  if (!unit.members.some((m) => m.status === "pending" && resolvedStatus(m.image_id) !== null)) return unit
  return {
    ...unit,
    members: unit.members.map((m) => {
      const next = m.status === "pending" ? resolvedStatus(m.image_id) : null
      return next ? { ...m, status: next } : m
    }),
  }
}

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

export default function IngestionReviewPage({ memesApi }: Props) {
  const [status, setStatus] = useState<IngestionRunStatus | null>(null)
  const [clusters, setClusters] = useState<IngestionCluster[]>([])
  const [tierBItems, setTierBItems] = useState<IngestionTierBReviewItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasNext, setHasNext] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [decisions, setDecisions] = useState<Record<string, Decision | undefined>>({})
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  // Keyed by the unit object, never a list index -- optimistic removal reshuffles indices
  // mid-request, so an index key would move "Submitting…" onto whatever unit slid into that slot.
  const [submitting, setSubmitting] = useState<ReviewUnit | "all" | null>(null)
  const [peek, setPeek] = useState<IngestionClusterMember | null>(null)
  // Big units render collapsed; expansion is keyed by unit identity (like `submitting`),
  // so a server reload -- which installs fresh unit objects -- naturally resets it.
  const [expandedUnits, setExpandedUnits] = useState<Set<ReviewUnit>>(new Set())
  const [confirmingAll, setConfirmingAll] = useState(false)
  const confirmAllTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const loadingMoreRef = useRef(false)

  const tier = status ? tierForStage(status.stage) : null

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await memesApi.getIngestionRunStatus()
      setStatus(s)
      setError(null)
      const t = s ? tierForStage(s.stage) : null
      // A server reload is authoritative: drop any local decision whose target is no longer a
      // pending, decidable image of the fresh queue (covers a decision resolve() silently skipped
      // -- one that came back in none of rejected/kept/failed/move_failed).
      let pendingIds: Set<string>
      if (t === "tier_b") {
        const pageResult = await memesApi.getIngestionTierBReview(undefined)
        setTierBItems(pageResult.items)
        setClusters([])
        setNextCursor(pageResult.next_cursor)
        setHasNext(pageResult.has_next)
        pendingIds = new Set(
          pageResult.items.flatMap((it) => [
            it.image.image_id,
            ...it.candidates.filter((c) => c.member.status === "pending").map((c) => c.member.image_id),
          ])
        )
      } else if (t) {
        const pageResult = await memesApi.getIngestionClusters(t, undefined)
        setClusters(pageResult.items)
        setTierBItems([])
        setNextCursor(pageResult.next_cursor)
        setHasNext(pageResult.has_next)
        pendingIds = new Set(
          pageResult.items.flatMap((c) => c.members.filter((m) => m.status === "pending").map((m) => m.image_id))
        )
      } else {
        setClusters([])
        setTierBItems([])
        setNextCursor(null)
        setHasNext(false)
        pendingIds = new Set()
      }
      setDecisions((prev) => {
        const next: Record<string, Decision | undefined> = {}
        for (const [id, d] of Object.entries(prev)) if (pendingIds.has(id)) next[id] = d
        return next
      })
    } catch (e: unknown) {
      setStatus(null)
      setClusters([])
      setTierBItems([])
      setNextCursor(null)
      setHasNext(false)
      setError(e instanceof Error ? e.message : "Failed to load ingestion review")
    } finally {
      setLoading(false)
    }
  }, [memesApi])

  const loadedRef = useRef(false)
  useEffect(() => { if (loadedRef.current) return; loadedRef.current = true; void load() }, [load])

  useEffect(() => () => {
    if (confirmAllTimeoutRef.current) clearTimeout(confirmAllTimeoutRef.current)
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
      if (tier === "tier_b") {
        const pageResult = await memesApi.getIngestionTierBReview(nextCursor)
        setTierBItems((prev) => [...prev, ...pageResult.items])
        setNextCursor(pageResult.next_cursor)
        setHasNext(pageResult.has_next)
      } else {
        const pageResult = await memesApi.getIngestionClusters(tier, nextCursor)
        setClusters((prev) => [...prev, ...pageResult.items])
        setNextCursor(pageResult.next_cursor)
        setHasNext(pageResult.has_next)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load more review items")
    } finally {
      loadingMoreRef.current = false
      setLoadingMore(false)
    }
  }, [memesApi, tier, nextCursor, hasNext, loadingMore])

  function setDecision(memberId: string, decision: Decision) {
    setDecisions((prev) => ({ ...prev, [memberId]: prev[memberId] === decision ? undefined : decision }))
  }

  function toggleExpand(unit: ReviewUnit) {
    setExpandedUnits((prev) => {
      const next = new Set(prev)
      if (next.has(unit)) next.delete(unit)
      else next.add(unit)
      return next
    })
  }

  // ---- submit paths (optimistic) ----

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

  // A Tier B item is "fully resolved" once its SUBJECT is decided -- candidates own their own
  // cards, so a decision on a candidate never removes the card it was shown on.
  function isFullyResolved(unit: ReviewUnit): boolean {
    if (isTierBItem(unit)) return decisions[unit.image.image_id] !== undefined
    const pending = unit.members.filter((m) => m.status === "pending")
    return pending.length > 0 && pending.every((m) => decisions[m.image_id] !== undefined)
  }

  async function runSubmit(which: ReviewUnit | "all", toSubmit: { unit: ReviewUnit; index: number }[]) {
    if (!tier) return
    // Bind the active list ONCE -- `tier` can't change mid-submit, whereas re-deriving from
    // `tierBItems.length` would flip to the cluster list the moment an optimistic removal empties it.
    const isTierB = tier === "tier_b"
    const setActive = (updater: (prev: ReviewUnit[]) => ReviewUnit[]) => {
      if (isTierB) setTierBItems((prev) => updater(prev) as IngestionTierBReviewItem[])
      else setClusters((prev) => updater(prev) as IngestionCluster[])
    }
    const payload = toSubmit.flatMap(({ unit }) => decidedPendingIn(unit))
    if (payload.length === 0) return
    // Units we optimistically pull out of the list now, with their original position for rollback.
    const removed = toSubmit.filter(({ unit }) => isFullyResolved(unit))
    const removedSet = new Set<ReviewUnit>(removed.map(({ unit }) => unit))
    setSubmitting(which)
    // Compute the post-removal visible count inside the updater -- reading it back from a ref
    // after the await races the passive effect that would sync the ref.
    let survivingCount = 0
    setActive((prev) => {
      const next = prev.filter((u) => !removedSet.has(u))
      survivingCount = next.length
      return next
    })
    try {
      const response: IngestionResolveResponse = await memesApi.resolveIngestionCluster(tier, payload)
      const failedIds = new Set(response.failed.map((f) => f.image_id))
      // Payload-scoped, response-time prune: clear every id in THIS submit's payload except the
      // ones the server reports as `failed` (those stay selected for retry). Race-free -- it
      // never touches a failed id, so it can't fight the re-insert below -- and it also clears a
      // decision the backend silently skipped (id in none of rejected/kept/failed/move_failed).
      setDecisions((prev) => {
        const next = { ...prev }
        for (const { image_id } of payload) if (!failedIds.has(image_id)) delete next[image_id]
        return next
      })
      const reinsert = failedIds.size > 0
        ? removed
            .filter(({ unit }) => unitImageIds(unit).some((id) => failedIds.has(id)))
            .slice()
            .sort((a, b) => a.index - b.index)
        : []
      // Members the server actually resolved (not failed) that are still sitting in a *surviving*
      // unit: flip their local status so their tile stops offering Keep/Reject. Use the per-id
      // server outcome so the status label stays truthful (rejected -> "rejected", kept -> "active").
      const rejectedIds = new Set(response.rejected.filter((id) => !failedIds.has(id)))
      const keptIds = new Set(response.kept.filter((id) => !failedIds.has(id)))
      const resolvedCount = rejectedIds.size + keptIds.size
      const resolvedStatus = (imageId: string): string | null =>
        rejectedIds.has(imageId) ? "rejected" : keptIds.has(imageId) ? "active" : null
      if (reinsert.length > 0 || resolvedCount > 0) {
        setActive((prev) => {
          let copy = [...prev]
          for (const { unit, index } of reinsert) copy.splice(Math.min(index, copy.length), 0, unit)
          if (resolvedCount > 0) copy = copy.map((u) => flipResolved(u, resolvedStatus))
          return copy
        })
      }
      // Ruling 3: reload only when the on-screen queue has fully emptied -- restores the old
      // auto-advance (Tier A -> Tier B, via load() also refetching run status) and, when more
      // pages exist, pulls the next unreviewed page-1 work in place of a bare "Load more" button.
      // Every submit that leaves units visible stays purely optimistic (no reload/scroll jump).
      const remaining = survivingCount + reinsert.length
      if (remaining === 0) {
        await load()
      }
      // Set the summary after any reload -- load()'s success path clears `error`, so setting it
      // first would have the reload immediately wipe a move-failed / partial-failure summary.
      setError(formatResolveSummary(response))
    } catch (e: unknown) {
      // Roll the optimistically-removed units back into place. Decisions were never touched
      // on the way out, so they're still selected -- nothing to restore there.
      setActive((prev) => {
        const copy = [...prev]
        for (const { unit, index } of removed.slice().sort((a, b) => a.index - b.index)) {
          copy.splice(Math.min(index, copy.length), 0, unit)
        }
        return copy
      })
      setError(e instanceof Error ? e.message : "Failed to submit decisions")
    } finally {
      setSubmitting(null)
    }
  }

  const activeUnits: ReviewUnit[] = tier === "tier_b" ? tierBItems : clusters
  const submitUnit = (unit: ReviewUnit, index: number) => runSubmit(unit, [{ unit, index }])
  const submitAll = () => runSubmit("all", activeUnits.map((unit, index) => ({ unit, index })))

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

  const { groupsWithPendingCount, allPendingCount } = useMemo(() => {
    let c = 0, i = 0
    const list: ReviewUnit[] = tier === "tier_b" ? tierBItems : clusters
    for (const unit of list) {
      const n = decidedPendingIn(unit).length
      if (n > 0) { c++; i += n }
    }
    return { groupsWithPendingCount: c, allPendingCount: i }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clusters, tierBItems, tier, decisions])

  // ---- render ----
  if (loading) return <Shell><p className="text-sm text-gray-400">Loading…</p></Shell>
  if (error && !status) return (
    <Shell>
      <p className="text-sm text-red-500 mb-3">{error}</p>
      <button className="text-sm rounded bg-blue-600 text-white px-3 py-1" onClick={() => void load()}>Retry</button>
    </Shell>
  )
  if (!status) return <Shell><p className="text-sm text-gray-400">No ingestion run is currently in progress.</p></Shell>

  return (
    <div>
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
      {tier === "tier_b" && tierBItems.length === 0 && !hasNext && (
        <p className="text-sm text-gray-400">No Tier B images need review right now.</p>
      )}
      {tier && tier !== "tier_b" && clusters.length === 0 && !hasNext && (
        <p className="text-sm text-gray-400">No {TIER_LABEL[tier]} clusters need review right now.</p>
      )}

      {tier === "tier_b" && tierBItems.length > 0 && (
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
              expanded={expandedUnits.has(item)}
              onToggleExpand={() => toggleExpand(item)}
              onPeek={setPeek}
            />
          )}
        />
      )}

      {tier && tier !== "tier_b" && clusters.length > 0 && (
        <Virtuoso
          useWindowScroll
          data={clusters}
          endReached={() => { void loadMore() }}
          increaseViewportBy={{ top: 600, bottom: 1600 }}
          itemContent={(index, cluster) => (
            <ClusterRow
              memesApi={memesApi}
              cluster={cluster}
              decisions={decisions}
              onDecide={setDecision}
              onSubmit={() => void submitUnit(cluster, index)}
              submitting={submitting === cluster || submitting === "all"}
              expanded={expandedUnits.has(cluster)}
              onToggleExpand={() => toggleExpand(cluster)}
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

      {groupsWithPendingCount > 0 && (() => {
        // Tier A is the established path -- its bar stays worded "cluster(s)". Tier B's per-image
        // cards aren't clusters, so that queue says "group(s)".
        const unitNoun = tier === "tier_b" ? "group" : "cluster"
        const units = `${groupsWithPendingCount} ${unitNoun}${groupsWithPendingCount === 1 ? "" : "s"}`
        const images = `${allPendingCount} image${allPendingCount === 1 ? "" : "s"}`
        return (
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
                  ? `Confirm? (${units}, ${images})`
                  : `Submit all decisions (${units}, ${images})`}
            </button>
          </div>
        )
      })()}

      {peek && (
        <Modal onClose={() => setPeek(null)} title={peek.filename}>
          <img src={memesApi.getImageUrlById(peek.image_id)} alt={peek.filename} className="max-w-full max-h-[80vh] object-contain" />
        </Modal>
      )}
    </div>
  )
}
