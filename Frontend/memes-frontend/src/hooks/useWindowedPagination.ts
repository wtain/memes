import { useCallback, useEffect, useRef, useState } from "react"
import type { Meme } from "../types/generated/all"

export type FetchDirection = "forward" | "backward"

export type FetchPageResult = {
  items: Meme[]
  nextCursor?: string
  hasNext?: boolean
  previousCursor?: string
}

export type FetchPageFn = (cursor: string | undefined, direction: FetchDirection) => Promise<FetchPageResult>

export type Page = {
  cursor: string | undefined
  items: Meme[]
}

export type UseWindowedPaginationOptions = {
  fetchPage: FetchPageFn
  initialCursor?: string
  /** Changing this re-fetches from scratch (mirrors filter/tagFilters changes today). */
  resetKey: string
  maxPages?: number
  supportsColdBackward?: boolean
}

export type UseWindowedPaginationResult = {
  pages: Page[]
  items: Meme[]
  firstItemIndex: number
  loading: boolean
  /** True after a page fetch failed; stays set (no automatic refetch) until retry() or a resetKey change. */
  error: boolean
  hasMoreForward: boolean
  hasMoreBackward: boolean
  loadForward: () => Promise<void>
  loadBackward: () => Promise<void>
  /** Clears the error and repeats the failed fetch (same cursor, same direction). */
  retry: () => void
}

const DEFAULT_MAX_PAGES = 4
const FIRST_ITEM_INDEX_START = 10_000

export function cursorForVirtualIndex(pages: Page[], firstItemIndex: number, virtualIndex: number): string | undefined {
  let localIndex = virtualIndex - firstItemIndex
  if (localIndex < 0) return pages[0]?.cursor
  for (const page of pages) {
    if (localIndex < page.items.length) return page.cursor
    localIndex -= page.items.length
  }
  return pages[pages.length - 1]?.cursor
}

export function useWindowedPagination({
  fetchPage,
  initialCursor,
  resetKey,
  maxPages = DEFAULT_MAX_PAGES,
  supportsColdBackward = false,
}: UseWindowedPaginationOptions): UseWindowedPaginationResult {
  const [pages, setPages] = useState<Page[]>([])
  const [firstItemIndex, setFirstItemIndex] = useState(FIRST_ITEM_INDEX_START)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [hasMoreForward, setHasMoreForward] = useState(true)
  const [hasMoreBackward, setHasMoreBackward] = useState(supportsColdBackward)

  const fetchPageRef = useRef(fetchPage)
  useEffect(() => { fetchPageRef.current = fetchPage })

  const pagesRef = useRef<Page[]>([])
  const loadingRef = useRef(false)
  const hasMoreForwardRef = useRef(true)
  const nextForwardCursorRef = useRef<string | undefined>(initialCursor)
  const visitedCursorsRef = useRef<(string | undefined)[]>([])
  const windowStartRef = useRef(0)
  const coldBackwardExhaustedRef = useRef(false)
  const initialCursorRef = useRef(initialCursor)
  const errorRef = useRef(false)
  const generationRef = useRef(0)
  const failedDirectionRef = useRef<FetchDirection | null>(null)

  const loadForward = useCallback(async () => {
    if (loadingRef.current || errorRef.current || !hasMoreForwardRef.current) return
    loadingRef.current = true
    setLoading(true)
    const generation = generationRef.current
    try {
      const cursor = nextForwardCursorRef.current
      const result = await fetchPageRef.current(cursor, "forward")
      // A resetKey change while this fetch was in flight: its result belongs to the old key.
      if (generation !== generationRef.current) return
      const newPage: Page = { cursor, items: result.items }
      visitedCursorsRef.current.push(cursor)
      nextForwardCursorRef.current = result.nextCursor
      hasMoreForwardRef.current = result.hasNext ?? false
      setHasMoreForward(hasMoreForwardRef.current)

      let nextPages = [...pagesRef.current, newPage]
      if (nextPages.length > maxPages) {
        const evicted = nextPages[0]
        nextPages = nextPages.slice(1)
        windowStartRef.current += 1
        setFirstItemIndex(idx => idx + evicted.items.length)
        // We've just evicted a page from the front, which means session-replay backward
        // (re-fetching that page via visitedCursorsRef) is now genuinely available -- mirrors
        // the symmetric `setHasMoreForward(true)` in loadBackward's own eviction branch below.
        setHasMoreBackward(true)
      }
      pagesRef.current = nextPages
      setPages(nextPages)
    } catch {
      // A rejection from before a resetKey change must not poison the new key.
      if (generation === generationRef.current) {
        errorRef.current = true
        failedDirectionRef.current = "forward"
        setError(true)
      }
    } finally {
      if (generation === generationRef.current) {
        loadingRef.current = false
        setLoading(false)
      }
    }
  }, [maxPages])

  const loadBackward = useCallback(async () => {
    if (loadingRef.current || errorRef.current) return
    const canReplay = windowStartRef.current > 0
    const canColdBackward = supportsColdBackward && !coldBackwardExhaustedRef.current
    if (!canReplay && !canColdBackward) return

    loadingRef.current = true
    setLoading(true)
    const generation = generationRef.current
    try {
      let cursor: string | undefined
      let items: Meme[]

      if (canReplay) {
        cursor = visitedCursorsRef.current[windowStartRef.current - 1]
        const result = await fetchPageRef.current(cursor, "forward")
        if (generation !== generationRef.current) return
        items = result.items
        windowStartRef.current -= 1
      } else {
        const anchor = pagesRef.current[0]?.cursor ?? initialCursorRef.current
        if (anchor === undefined) {
          // No real anchor to page backward from -- we're already at the true beginning
          // (either this is the very first page ever fetched, or initialCursor was never set).
          coldBackwardExhaustedRef.current = true
          setHasMoreBackward(false)
          return
        }
        const result = await fetchPageRef.current(anchor, "backward")
        if (generation !== generationRef.current) return
        if (result.items.length === 0) {
          coldBackwardExhaustedRef.current = true
          setHasMoreBackward(false)
          return
        }
        if (result.previousCursor === undefined) {
          // This page itself reached the true beginning -- prepend it, but there's
          // nothing further back after this.
          coldBackwardExhaustedRef.current = true
        }
        cursor = result.previousCursor
        items = result.items
        visitedCursorsRef.current.unshift(cursor)
      }

      const newPage: Page = { cursor, items }
      let nextPages = [newPage, ...pagesRef.current]
      setFirstItemIndex(idx => idx - newPage.items.length)

      if (nextPages.length > maxPages) {
        const evictedBack = nextPages[nextPages.length - 1]
        nextPages = nextPages.slice(0, -1)
        // Keep the replay log and forward frontier in sync with the evicted page: pop its
        // visited-cursor entry (so a later loadBackward replay doesn't desync from windowStartRef
        // and fetch a stale page), and rewind nextForwardCursorRef to the cursor that originally
        // fetched it (so the next loadForward() re-fetches that same content instead of silently
        // skipping past it). hasMoreForward is forced back to true because we've just proven
        // there's more forward content available -- the page we evicted -- regardless of what an
        // earlier fetch's hasNext said.
        visitedCursorsRef.current.pop()
        nextForwardCursorRef.current = evictedBack.cursor
        hasMoreForwardRef.current = true
        setHasMoreForward(true)
      }

      pagesRef.current = nextPages
      setPages(nextPages)
      setHasMoreBackward(windowStartRef.current > 0 || (supportsColdBackward && !coldBackwardExhaustedRef.current))
    } catch {
      // A rejection from before a resetKey change must not poison the new key.
      if (generation === generationRef.current) {
        errorRef.current = true
        failedDirectionRef.current = "backward"
        setError(true)
      }
    } finally {
      if (generation === generationRef.current) {
        loadingRef.current = false
        setLoading(false)
      }
    }
  }, [maxPages, supportsColdBackward])

  const loadForwardRef = useRef(loadForward)
  useEffect(() => { loadForwardRef.current = loadForward })

  const loadBackwardRef = useRef(loadBackward)
  useEffect(() => { loadBackwardRef.current = loadBackward })

  const retry = useCallback(() => {
    if (!errorRef.current) return
    const direction = failedDirectionRef.current
    errorRef.current = false
    failedDirectionRef.current = null
    setError(false)
    if (direction === "backward") void loadBackwardRef.current()
    else void loadForwardRef.current()
  }, [])

  useEffect(() => {
    // Reset all bookkeeping and re-fetch from scratch.
    generationRef.current += 1
    pagesRef.current = []
    loadingRef.current = false
    hasMoreForwardRef.current = true
    nextForwardCursorRef.current = initialCursor
    initialCursorRef.current = initialCursor
    visitedCursorsRef.current = []
    windowStartRef.current = 0
    coldBackwardExhaustedRef.current = false
    errorRef.current = false
    failedDirectionRef.current = null

    // `await Promise.resolve()` before the first setState defers it past the synchronous
    // effect body, per the react-hooks/set-state-in-effect rule (same pattern used in
    // AdminBatchesPage.tsx).
    void (async () => {
      await Promise.resolve()
      setPages([])
      setFirstItemIndex(FIRST_ITEM_INDEX_START)
      setLoading(false)
      setError(false)
      setHasMoreForward(true)
      setHasMoreBackward(supportsColdBackward)

      loadForwardRef.current()
    })()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey])

  const items = pages.flatMap(p => p.items)

  return { pages, items, firstItemIndex, loading, error, hasMoreForward, hasMoreBackward, loadForward, loadBackward, retry }
}
