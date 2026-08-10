import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { useWindowedPagination, cursorForVirtualIndex, type FetchPageFn, type Page } from "../hooks/useWindowedPagination"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
  initialCursor?: string
  onCursorChange?: (cursor: string | undefined) => void
}

type ClusterRow = { clusterId: number | string; members: Meme[] }

const CURSOR_DEBOUNCE_MS = 300
const ROW_FIRST_ITEM_INDEX_START = 10_000

function distinctClusterCount(page: Page): number {
  return new Set(page.items.map(m => m.clusterId ?? "unknown")).size
}

export function MemesDuplicatesList({ memesApi, initialCursor, onCursorChange }: Props) {
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)
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

  const { pages, items, firstItemIndex, hasMoreForward, hasMoreBackward, loadForward, loadBackward } = useWindowedPagination({
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
  const clusterRows: ClusterRow[] = useMemo(() => {
    const map = new Map<number | string, Meme[]>()
    const order: (number | string)[] = []
    for (const meme of items) {
      const key = meme.clusterId ?? "unknown"
      if (!map.has(key)) { map.set(key, []); order.push(key) }
      map.get(key)!.push(meme)
    }
    return order.map(clusterId => ({ clusterId, members: map.get(clusterId)! }))
  }, [items])

  const prevPagesRef = useRef<Page[]>([])
  const [rowFirstItemIndex, setRowFirstItemIndex] = useState(ROW_FIRST_ITEM_INDEX_START)

  useEffect(() => {
    const prevPages = prevPagesRef.current
    prevPagesRef.current = pages

    if (prevPages.length === 0 || pages.length === 0) return
    if (pages[0].cursor === prevPages[0].cursor) return

    const prevFirstStillPresentAt = pages.findIndex(p => p.cursor === prevPages[0].cursor)
    if (prevFirstStillPresentAt > 0) {
      const prependedRowCount = pages.slice(0, prevFirstStillPresentAt).reduce((sum, p) => sum + distinctClusterCount(p), 0)
      setRowFirstItemIndex(idx => idx - prependedRowCount)
    } else {
      setRowFirstItemIndex(idx => idx + distinctClusterCount(prevPages[0]))
    }
  }, [pages])

  const handleRangeChanged = useCallback((range: { startIndex: number }) => {
    if (!onCursorChangeRef.current) return
    if (cursorChangeTimerRef.current) clearTimeout(cursorChangeTimerRef.current)
    cursorChangeTimerRef.current = setTimeout(() => {
      const cursor = cursorForVirtualIndex(pages, firstItemIndex, range.startIndex)
      onCursorChangeRef.current?.(cursor)
    }, CURSOR_DEBOUNCE_MS)
  }, [pages, firstItemIndex])

  return (
    <div>
      <Virtuoso
        useWindowScroll
        firstItemIndex={rowFirstItemIndex}
        data={clusterRows}
        startReached={() => { if (hasMoreBackward) loadBackward() }}
        endReached={() => { if (hasMoreForward) loadForward() }}
        rangeChanged={handleRangeChanged}
        itemContent={(_index, row) => (
          <div>
            <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
              {row.members.map(meme => (
                <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
              ))}
            </div>
            <hr className="my-4 border-gray-300" />
          </div>
        )}
      />

      {selectedMeme && (
        <MemeDetailsModal meme={selectedMeme} onClose={() => setSelectedMeme(null)} memesApi={memesApi} />
      )}

      {clusterRows.length === 0 && (
        <div className="h-10 flex items-center justify-center">
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}
