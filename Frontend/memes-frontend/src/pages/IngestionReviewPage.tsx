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
// used to decide whether a `failed` server response should re-insert an optimistically-removed
// unit. This includes non-pending members/candidates too -- harmless, not imprecise: `failedIds`
// (the only thing this list is ever tested against) can only ever contain ids that were actually
// submitted, and `decidedPendingIn` only ever emits *pending* members/candidates into a payload,
// so a non-pending id from this list can never match a `failedIds` entry.
function unitImageIds(u: ReviewUnit): string[] {
  return isTierBItem(u)
    ? [u.image.image_id, ...u.candidates.map((c) => c.member.image_id)]
    : u.members.map((m) => m.image_id)
}

// After a submit, flip the local status of members/candidates the server actually resolved (not
// `failed`) so their tiles stop offering Keep/Reject -- otherwise, with the decision highlight
// pruned, a still-pending-looking tile reads as "my submit didn't take". The status *label* stays
// truthful (a rejected id -> "rejected", a kept id -> "active") via the per-id `resolvedStatus`.
// Generic over the concrete unit type so callers mapping a concretely-typed array (e.g.
// `IngestionTierBReviewItem[]`) keep that concrete type back, with no cast at the call site.
function flipResolved<T extends ReviewUnit>(unit: T, resolvedStatus: (id: string) => string | null): T {
  if (isTierBItem(unit)) {
    const imgNext = unit.image.status === "pending" ? resolvedStatus(unit.image.image_id) : null
    const candNeedsFlip = unit.candidates.some(
      (c) => c.member.status === "pending" && resolvedStatus(c.member.image_id) !== null
    )
    if (!imgNext && !candNeedsFlip) return unit
    // The spread-and-override below reconstructs the same concrete shape `unit` was narrowed to
    // (IngestionTierBReviewItem) -- TS can't see through the spread to re-derive that it's still
    // exactly T, hence the assertion; the value itself is exactly the T-shaped object it started as.
    return {
      ...unit,
      image: imgNext ? { ...unit.image, status: imgNext } : unit.image,
      candidates: unit.candidates.map((c) => {
        const next = c.member.status === "pending" ? resolvedStatus(c.member.image_id) : null
        return next ? { ...c, member: { ...c.member, status: next } } : c
      }),
    } as T
  }
  if (!unit.members.some((m) => m.status === "pending" && resolvedStatus(m.image_id) !== null)) return unit
  return {
    ...unit,
    members: unit.members.map((m) => {
      const next = m.status === "pending" ? resolvedStatus(m.image_id) : null
      return next ? { ...m, status: next } : m
    }),
  } as T
}

// Splices previously-removed units back into a concretely-typed array (reinsert-after-failure,
// or full rollback on a thrown request). `units` was collected from THIS call's own `toSubmit`
// (`submitUnit`/`submitAll` always build it from a single tier's active list), so every entry is
// always the same concrete shape as `copy` here -- a same-call invariant, not an actual type
// hazard, but not one TS can see through generically, hence the one assertion.
function spliceIn<T extends ReviewUnit>(copy: T[], units: { unit: ReviewUnit; index: number }[]): T[] {
  for (const { unit, index } of units) copy.splice(Math.min(index, copy.length), 0, unit as T)
  return copy
}

// The response-time transform applied to a review list after a submit: re-insert any units the
// server reported as `failed` (kept selected for retry), then -- if anything actually resolved --
// drop any tier-B unit whose own subject was just resolved (see the zombie-card note at its call
// site) and flip the local status of every other newly-resolved member/candidate. Generic and
// pure so it can be called twice with the identical arguments: once against a plain `ReviewUnit[]`
// just to read `.length` for the reload-when-empty check, and once per concrete setState branch
// to actually apply the update -- see `runSubmit`.
function resolveUnits<T extends ReviewUnit>(
  prev: T[],
  reinsert: { unit: ReviewUnit; index: number }[],
  resolvedCount: number,
  resolvedStatus: (id: string) => string | null
): T[] {
  let copy = spliceIn(prev.slice(), reinsert)
  if (resolvedCount > 0) {
    // A tier-B unit whose own SUBJECT was just resolved -- via *any* card's submit, not
    // necessarily its own -- can never become isFullyResolved() again: its decision was already
    // pruned in the caller, and its pairs are already fully settled via the subject's own
    // mark_reviewed/reject_image cascade. Drop it instead of flipping-and-stranding it, so the
    // reload-when-empty gate in `runSubmit` still fires. (Tier A is unaffected -- its clusters are
    // disjoint union-find components, so no image ever appears on two cluster cards.)
    copy = copy.filter(
      (u) => !(isTierBItem(u) && u.image.status === "pending" && resolvedStatus(u.image.image_id) !== null)
    )
    copy = copy.map((u) => flipResolved(u, resolvedStatus))
  }
  return copy
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

function StatusBanner({ status, tier, baseline }: {
  status: IngestionRunStatus | null
  tier: IngestionTier | null
  baseline: { tierRemaining: number; blockedTotal: number } | null
}) {
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
      {status.tier_remaining !== null && (
        <div className="mt-2 text-sm text-gray-700">
          <span className="font-semibold">{status.tier_remaining}</span>
          {" "}image{status.tier_remaining === 1 ? "" : "s"} still need{status.tier_remaining === 1 ? "s" : ""}{" "}
          {tier ? TIER_LABEL[tier] : ""} review
          {status.blocked_total !== null && (
            <span className="text-gray-500"> · {status.blocked_total} blocked from promotion</span>
          )}
          {baseline && (
            <span className="text-gray-400 ml-2">
              (-{Math.max(0, baseline.tierRemaining - status.tier_remaining)} since you opened this page)
            </span>
          )}
        </div>
      )}
      <div className="mt-2 flex gap-4 text-xs text-gray-400">
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
  // Captured once, the first time `load()` sees a non-null tier_remaining -- the "since you
  // opened this page" baseline. A ref (not state) because it must NOT trigger a re-render or
  // reset on every load() call, only ever be set the first time.
  const progressBaselineRef = useRef<{ tierRemaining: number; blockedTotal: number } | null>(null)

  const tier = status ? tierForStage(status.stage) : null

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const s = await memesApi.getIngestionRunStatus()
      setStatus(s)
      if (s && s.tier_remaining !== null && progressBaselineRef.current === null) {
        progressBaselineRef.current = { tierRemaining: s.tier_remaining, blockedTotal: s.blocked_total ?? 0 }
      }
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
    // `tier` can't change mid-submit (unlike re-deriving from `tierBItems.length`, which would
    // flip to the cluster list the moment an optimistic removal empties it), so it's safe to read
    // once and dispatch every update to the one active setState below. Each `apply*` updater is
    // written generically (`<T extends ReviewUnit>`) so it can be handed straight to either
    // `setTierBItems` or `setClusters` -- TS instantiates T from whichever one actually gets
    // called, so neither call needs a cast to bridge `ReviewUnit[]` back to a concrete array type.
    const isTierB = tier === "tier_b"
    // Fix: dedupe by image_id. The same image can be a pending candidate shown on one card and
    // the subject of its own card (or a pending candidate on two different cards) -- a multi-unit
    // submit ("Submit all") must send/count it once, not once per card it happens to appear on.
    const payloadMap = new Map<string, { image_id: string; decision: Decision }>()
    for (const { unit } of toSubmit) for (const d of decidedPendingIn(unit)) payloadMap.set(d.image_id, d)
    const payload = [...payloadMap.values()]
    if (payload.length === 0) return
    // Units we optimistically pull out of the list now, with their original position for rollback.
    const removed = toSubmit.filter(({ unit }) => isFullyResolved(unit))
    const removedSet = new Set<ReviewUnit>(removed.map(({ unit }) => unit))
    setSubmitting(which)
    // Compute the post-removal visible list+count inside the updater -- reading it back from a
    // ref after the await races the passive effect that would sync the ref. This dispatch happens
    // before the `await` below, in the same synchronous batch as the click handler that triggered
    // it, which is what makes reading `survivingCount`/`activeAfterRemoval` back out immediately
    // reliable; a later updater dispatched *after* the await can't be read back the same way (see
    // `finalCount` below), so it's computed independently instead.
    let survivingCount = 0
    let activeAfterRemoval: ReviewUnit[] = []
    const applyRemoval = <T extends ReviewUnit>(prev: T[]): T[] => {
      const next = prev.filter((u) => !removedSet.has(u))
      survivingCount = next.length
      activeAfterRemoval = next
      return next
    }
    if (isTierB) setTierBItems(applyRemoval)
    else setClusters(applyRemoval)
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
      // Fix: `finalCount` (not the earlier `survivingCount`) drives the reload-when-empty check
      // below -- it accounts for the zombie-card drop just below, which `survivingCount` predates.
      // Computed via `resolveUnits` applied directly to the already-captured `activeAfterRemoval`
      // array, NOT by reading a value back out of the setState call a few lines down: that call is
      // dispatched after the `await` above, outside the click handler's original synchronous
      // batch, so (unlike `applyRemoval` above) there's no guarantee its updater has already run
      // by the time the very next line executes.
      let finalCount = survivingCount
      if (reinsert.length > 0 || resolvedCount > 0) {
        finalCount = resolveUnits(activeAfterRemoval, reinsert, resolvedCount, resolvedStatus).length
        if (isTierB) setTierBItems((prev) => resolveUnits(prev, reinsert, resolvedCount, resolvedStatus))
        else setClusters((prev) => resolveUnits(prev, reinsert, resolvedCount, resolvedStatus))
      }
      // Ruling 3: reload only when the on-screen queue has fully emptied -- restores the old
      // auto-advance (Tier A -> Tier B, via load() also refetching run status) and, when more
      // pages exist, pulls the next unreviewed page-1 work in place of a bare "Load more" button.
      // Every submit that leaves units visible stays purely optimistic (no reload/scroll jump).
      let submitDeltaMessage: string | null = null
      if (finalCount === 0) {
        await load()
      } else {
        // The visible queue didn't empty, so a full reload isn't warranted -- but the progress
        // numbers (tier_remaining/blocked_total) did change. Refresh just the status, not the
        // whole page, so the banner reflects this submit instead of going stale until the queue
        // happens to empty.
        try {
          const prevRemaining = status?.tier_remaining ?? null
          const freshStatus = await memesApi.getIngestionRunStatus()
          setStatus(freshStatus)
          if (prevRemaining !== null && freshStatus?.tier_remaining != null) {
            const delta = prevRemaining - freshStatus.tier_remaining
            if (delta > 0) {
              submitDeltaMessage = `${delta} fewer image${delta === 1 ? "" : "s"} remaining in ${tier === "tier_b" ? TIER_LABEL.tier_b : TIER_LABEL.tier_a} after that submit`
            }
          }
        } catch {
          // Best-effort -- a failed progress refresh shouldn't surface as a page error or block
          // a submit that already succeeded.
        }
      }
      // Set the summary after any reload -- load()'s success path clears `error`, so setting it
      // first would have the reload immediately wipe a move-failed / partial-failure summary.
      const resolveSummary = formatResolveSummary(response)
      setError([submitDeltaMessage, resolveSummary].filter(Boolean).join("; ") || null)
    } catch (e: unknown) {
      // Roll the optimistically-removed units back into place. Decisions were never touched
      // on the way out, so they're still selected -- nothing to restore there.
      const rollbackUnits = removed.slice().sort((a, b) => a.index - b.index)
      const applyRollback = <T extends ReviewUnit>(prev: T[]): T[] => spliceIn(prev.slice(), rollbackUnits)
      if (isTierB) setTierBItems(applyRollback)
      else setClusters(applyRollback)
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
    // `groupsWithPendingCount` stays a per-card count (correct as-is -- it's cards, not images).
    // `allPendingCount` dedupes by image_id: the same image can be a decided pending candidate on
    // one card and the subject of its own card (or a pending candidate on two different cards),
    // so summing decidedPendingIn().length across cards would double-count it -- misleading on the
    // "Submit all" bar and its destructive-confirm step.
    let c = 0
    const decidedIds = new Set<string>()
    const list: ReviewUnit[] = tier === "tier_b" ? tierBItems : clusters
    for (const unit of list) {
      const decided = decidedPendingIn(unit)
      if (decided.length > 0) { c++; for (const d of decided) decidedIds.add(d.image_id) }
    }
    return { groupsWithPendingCount: c, allPendingCount: decidedIds.size }
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
      {/* progressBaselineRef is read here for display only, never for a reactivity decision: it's
          set at most once (in load(), guarded by `=== null`) and every write to it lands in the
          same tick as a setStatus() call, which already re-renders this component -- so this read
          can never observe a stale value between renders. */}
      {/* eslint-disable-next-line react-hooks/refs -- see comment above */}
      <StatusBanner status={status} tier={tier} baseline={progressBaselineRef.current} />
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
