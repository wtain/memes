import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { useWindowedPagination, cursorForVirtualIndex, type FetchPageFn, type Page } from "../hooks/useWindowedPagination"
import type { MemesApi } from "../api/MemesApi"
import type { Meme, DuplicatePair } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
  initialCursor?: string
  onCursorChange?: (cursor: string | undefined) => void
}

type ClusterRow = { clusterId: number | string; members: Meme[] }

const CURSOR_DEBOUNCE_MS = 300
const ROW_FIRST_ITEM_INDEX_START = 10_000

function clusterIdsIn(pages: Page[]): Set<number | string> {
  return new Set(pages.flatMap(p => p.items).map(m => m.clusterId ?? "unknown"))
}

export function MemesDuplicatesList({ memesApi, initialCursor, onCursorChange }: Props) {
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)
  // Keyed by clusterId -- the presence of an entry means that row is dismissed, and its
  // value is exactly the pairs to pass to undoDismissDuplicates for THAT row's in-place
  // Undo button. A single shared toast previously meant only the most-recently-dismissed
  // cluster could ever be undone; this keeps every dismissed row independently undoable.
  const [dismissedClusters, setDismissedClusters] = useState<Map<number, DuplicatePair[]>>(new Map())
  const cursorChangeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onCursorChangeRef = useRef(onCursorChange)
  useEffect(() => { onCursorChangeRef.current = onCursorChange })

  const fetchPage: FetchPageFn = useCallback(async (cursor, direction) => {
    // Only pass `direction` through when it's "backward" -- passing "forward" explicitly on
    // every normal forward load would add a 4th positional arg the rest of the app (and this
    // component's own tests) don't expect for the common case; `iterateDuplicates` already
    // defaults to forward behavior when the param is omitted.
    const response = direction === "backward"
      ? await memesApi.iterateDuplicates(40, cursor, 0.2, "backward")
      : await memesApi.iterateDuplicates(40, cursor, 0.2)
    return {
      items: (response.items ?? []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] })),
      nextCursor: response.nextCursor,
      hasNext: response.hasNext,
      previousCursor: response.previousCursor,
    }
  }, [memesApi])

  const { pages, items, firstItemIndex, loading, hasMoreForward, hasMoreBackward, loadForward, loadBackward } = useWindowedPagination({
    fetchPage,
    initialCursor,
    resetKey: "duplicates",
    supportsColdBackward: true,
  })

  // Group the currently-windowed items into whole clusters, in the order
  // they first appear. A cluster still being filled in by an in-flight
  // adjacent page briefly renders with fewer members than it truly has --
  // accepted, self-heals as soon as that page loads (same characteristic
  // the pre-existing unwindowed implementation already had).
  //
  // `rowStartItemOffsets[i]` is the item-space offset (relative to the start of `items`) of
  // row i's first member -- needed because Virtuoso's `rangeChanged` reports `startIndex` in
  // row-space (this instance is fed `data={clusterRows}`), but `cursorForVirtualIndex` requires
  // an index in the same item-space as the hook's `firstItemIndex`. Built in the same pass as
  // the grouping so it can't drift from `clusterRows`.
  const { clusterRows, rowStartItemOffsets } = useMemo(() => {
    const map = new Map<number | string, Meme[]>()
    const order: (number | string)[] = []
    for (const meme of items) {
      const key = meme.clusterId ?? "unknown"
      if (!map.has(key)) { map.set(key, []); order.push(key) }
      map.get(key)!.push(meme)
    }
    const rows: ClusterRow[] = order.map(clusterId => ({ clusterId, members: map.get(clusterId)! }))
    const offsets: number[] = []
    let cumulative = 0
    for (const row of rows) {
      offsets.push(cumulative)
      cumulative += row.members.length
    }
    return { clusterRows: rows, rowStartItemOffsets: offsets }
  }, [items])

  const [rowFirstItemIndex, setRowFirstItemIndex] = useState(ROW_FIRST_ITEM_INDEX_START)

  // Computed synchronously during render (not in a useEffect) so `rowFirstItemIndex` never gets
  // out of sync with `clusterRows` for even one render. An effect-based version would react to
  // `pages` changing on render N, but by then `clusterRows` (derived from `items`, which is
  // derived from `pages`) has ALREADY reflected the new pages in that same render N -- the
  // effect wouldn't run and update `rowFirstItemIndex` until render N+1, so Virtuoso would
  // briefly receive the new `clusterRows` paired with the stale `rowFirstItemIndex`, which it
  // misreads as a change at the wrong end of the list and defeats the jump-free-prepend
  // mechanism this whole feature exists to provide. Calling the setter here, during render, is
  // React's documented "adjusting state when a prop changes" pattern: React discards this
  // render's output and re-renders immediately with the new state, before anything is painted,
  // so `rowFirstItemIndex` and `clusterRows` are always consistent within the same commit. This
  // deliberately uses `useState` (not `useRef`) to track the previous `pages` value -- this
  // repo's `react-hooks/refs` lint rule forbids reading/writing a ref's `.current` during render
  // (refs are meant for effects/handlers only), but reading and setting *state* mid-render is the
  // sanctioned mechanism per React's own docs for exactly this "adjust state when a prop changes"
  // case, so it isn't flagged.
  //
  // The row-count delta is computed as a *set difference* of cluster IDs across the whole
  // previous/current window, not by summing `distinctClusterCount` over just the added/evicted
  // page(s). A cluster that straddles a page boundary (e.g. its first member lands in the page
  // being evicted, its second in the page right after) still has a surviving row after the
  // eviction -- only its *other* member disappeared, not the row itself. Counting that page's
  // "distinct clusters touched" as if each one lost a whole row over-counts by one for every
  // straddling cluster, which -- given typical 2-3 member clusters against a 40-item page size --
  // is the common case, not an edge case: it produced a real, frequent scroll jump (regression,
  // see MemesDuplicatesList.test.tsx).
  const [prevPagesForRowIndex, setPrevPagesForRowIndex] = useState<Page[] | null>(null)
  if (prevPagesForRowIndex !== pages) {
    if (prevPagesForRowIndex !== null && pages.length > 0 && prevPagesForRowIndex.length > 0 && pages[0].cursor !== prevPagesForRowIndex[0].cursor) {
      const prevFirstStillPresentAt = pages.findIndex(p => p.cursor === prevPagesForRowIndex[0].cursor)
      if (prevFirstStillPresentAt > 0) {
        // Prepend: rows that are newly present now but weren't anywhere in the old window.
        const prevClusterIds = clusterIdsIn(prevPagesForRowIndex)
        const newClusterIds = clusterIdsIn(pages)
        let addedRows = 0
        for (const id of newClusterIds) if (!prevClusterIds.has(id)) addedRows++
        setRowFirstItemIndex(idx => idx - addedRows)
      } else if (prevFirstStillPresentAt === -1) {
        // Eviction: rows that were in the old window but are gone from the new one entirely
        // (a straddling cluster whose other member survives is correctly excluded here).
        const prevClusterIds = clusterIdsIn(prevPagesForRowIndex)
        const newClusterIds = clusterIdsIn(pages)
        let removedRows = 0
        for (const id of prevClusterIds) if (!newClusterIds.has(id)) removedRows++
        setRowFirstItemIndex(idx => idx + removedRows)
      }
    }
    setPrevPagesForRowIndex(pages)
  }

  const handleRangeChanged = useCallback((range: { startIndex: number }) => {
    if (!onCursorChangeRef.current) return
    if (cursorChangeTimerRef.current) clearTimeout(cursorChangeTimerRef.current)
    cursorChangeTimerRef.current = setTimeout(() => {
      // Convert `range.startIndex` from row-space to item-space before handing it to
      // `cursorForVirtualIndex`, which requires an index in the same item-space as `firstItemIndex`.
      const rowIndex = range.startIndex - rowFirstItemIndex
      const clampedRowIndex = Math.max(0, Math.min(rowIndex, rowStartItemOffsets.length - 1))
      const itemOffset = rowStartItemOffsets[clampedRowIndex] ?? 0
      const cursor = cursorForVirtualIndex(pages, firstItemIndex, firstItemIndex + itemOffset)
      onCursorChangeRef.current?.(cursor)
    }, CURSOR_DEBOUNCE_MS)
  }, [pages, firstItemIndex, rowFirstItemIndex, rowStartItemOffsets])

  const handleDismiss = useCallback(async (clusterId: number) => {
    try {
      const response = await memesApi.dismissDuplicateCluster(clusterId)
      setDismissedClusters(prev => new Map(prev).set(clusterId, response.pairs))
    } catch {
      // Left silent -- a failed dismiss just leaves the row showing as before, no
      // separate error UI for this first version.
    }
  }, [memesApi])

  const handleUndoCluster = useCallback(async (clusterId: number) => {
    const pairs = dismissedClusters.get(clusterId)
    if (!pairs) return
    await memesApi.undoDismissDuplicates(pairs)
    setDismissedClusters(prev => {
      const next = new Map(prev)
      next.delete(clusterId)
      return next
    })
  }, [memesApi, dismissedClusters])

  useEffect(() => {
    return () => {
      if (cursorChangeTimerRef.current) clearTimeout(cursorChangeTimerRef.current)
    }
  }, [])

  useEffect(() => {
    // This page deliberately preserves scroll position across a genuine return-to-this-page
    // navigation (initialCursor carries the resume point via the URL) -- but on a truly fresh
    // open (no cursor), the browser's leftover window scroll position from wherever the user
    // was before navigating here is still in effect. Virtuoso's useWindowScroll mode
    // reconciles its freshly-loaded, still-short content against that leftover offset, which
    // is what shows up as rapid scroll-jumping right after mount and a wrong cursor getting
    // written to the URL almost immediately, instead of starting from the beginning. Mirrors
    // MemesList.tsx's unconditional window.scrollTo-on-reset (MemesList never has a cursor to
    // resume from, so its version doesn't need this guard).
    if (initialCursor === undefined) {
      window.scrollTo({ top: 0 })
    }
    // Deliberately run once, using only the mount-time value of initialCursor -- must not
    // react to initialCursor changing later from this component's own scroll-driven URL sync
    // in the parent (ExploreDuplicatesPage), which would otherwise re-trigger this on every
    // debounced cursor update once it round-trips back down as a changed prop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div>
      <Virtuoso
        useWindowScroll
        firstItemIndex={rowFirstItemIndex}
        data={clusterRows}
        startReached={() => { if (hasMoreBackward) loadBackward() }}
        endReached={() => { if (hasMoreForward) loadForward() }}
        rangeChanged={handleRangeChanged}
        // Cluster rows vary wildly in height (2 members vs. dozens), and Virtuoso estimates
        // not-yet-rendered rows using a rolling default -- when a huge row is far outside that
        // estimate, measuring it forces a scroll-position correction, which is what's visible as
        // a jump. Rendering further ahead in both directions gives Virtuoso more lead time to
        // measure an outlier row's real height before the user's scroll position depends on it.
        // minOverscanItemCount is row-count-based (not pixel-based) specifically because a single
        // huge row can itself exceed a pixel-based buffer -- see react-virtuoso's own docs for
        // that prop, which calls out exactly this "dynamic or very tall content" case.
        increaseViewportBy={{ top: 600, bottom: 1200 }}
        minOverscanItemCount={{ top: 2, bottom: 4 }}
        itemContent={(_index, row) => {
          const isDismissed = typeof row.clusterId === "number" && dismissedClusters.has(row.clusterId)
          if (isDismissed) {
            return (
              <div>
                <div className="py-3 flex items-center gap-3">
                  <div className="flex -space-x-2">
                    {row.members.map(meme => (
                      <img
                        key={meme.id}
                        src={memesApi.getImageUrl(meme)}
                        alt={meme.id}
                        className="w-10 h-10 object-cover rounded border-2 border-white"
                        loading="lazy"
                      />
                    ))}
                  </div>
                  <span className="text-sm text-gray-400 italic">Marked as not duplicates</span>
                  <button
                    className="text-xs rounded bg-gray-100 px-3 py-1 hover:bg-gray-200"
                    onClick={() => handleUndoCluster(row.clusterId as number)}
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
              <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                {row.members.map(meme => (
                  <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
                ))}
              </div>
              {typeof row.clusterId === "number" && (
                <button
                  className="mt-2 text-xs rounded bg-gray-100 px-3 py-1 hover:bg-gray-200"
                  onClick={() => handleDismiss(row.clusterId as number)}
                >
                  Not duplicates
                </button>
              )}
              <hr className="my-4 border-gray-300" />
            </div>
          )
        }}
      />

      {selectedMeme && (
        <MemeDetailsModal meme={selectedMeme} onClose={() => setSelectedMeme(null)} memesApi={memesApi} />
      )}

      {loading && clusterRows.length > 0 && (
        <div className="h-10 flex items-center justify-center"><span>Loading...</span></div>
      )}

      {clusterRows.length === 0 && !loading && (
        <div className="h-10 flex items-center justify-center">
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}
