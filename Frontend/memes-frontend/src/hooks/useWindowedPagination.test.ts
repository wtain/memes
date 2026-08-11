import { renderHook, act, waitFor } from '@testing-library/react'
import { useWindowedPagination, cursorForVirtualIndex, type FetchPageFn, type Page } from './useWindowedPagination'
import type { Meme } from '../types/generated/all'

function makeMemes(prefix: string, count: number): Meme[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `${prefix}-${i}`, imageUrl: `/images/${prefix}-${i}.jpg`, text: [], tags: [],
  }))
}

describe('useWindowedPagination', () => {
  it('fetches the first page automatically on mount with no cursor', async () => {
    const fetchPage = vi.fn<FetchPageFn>().mockResolvedValue({
      items: makeMemes('p0', 3), nextCursor: 'c1', hasNext: true,
    })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k' }))

    await waitFor(() => expect(result.current.items).toHaveLength(3))

    expect(fetchPage).toHaveBeenCalledWith(undefined, 'forward')
    expect(result.current.hasMoreForward).toBe(true)
  })

  it('evicts the oldest page and shifts firstItemIndex once maxPages is exceeded', async () => {
    const fetchPage = vi.fn<FetchPageFn>()
      .mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p1', 2), nextCursor: 'c2', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k', maxPages: 2 }))

    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['p0-0', 'p0-1'])) // auto-loaded
    await act(async () => { await result.current.loadForward() }) // p1
    const indexBeforeEviction = result.current.firstItemIndex
    await act(async () => { await result.current.loadForward() }) // p2 -- evicts p0

    // p0 (2 items) evicted; only p1 + p2 remain (4 items), firstItemIndex advanced by 2.
    expect(result.current.items.map(m => m.id)).toEqual(['p1-0', 'p1-1', 'p2-0', 'p2-1'])
    expect(result.current.firstItemIndex).toBe(indexBeforeEviction + 2)
  })

  it('raises hasMoreBackward once loadForward evicts a page from the front, even without supportsColdBackward', async () => {
    const fetchPage = vi.fn<FetchPageFn>()
      .mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p1', 2), nextCursor: 'c2', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k', maxPages: 2 }))

    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['p0-0', 'p0-1'])) // auto-loaded
    expect(result.current.hasMoreBackward).toBe(false) // no supportsColdBackward, no replay history yet

    await act(async () => { await result.current.loadForward() }) // p1 -- still within maxPages, no eviction yet
    expect(result.current.hasMoreBackward).toBe(false)

    await act(async () => { await result.current.loadForward() }) // p2 -- evicts p0 from the front

    // p0 was just evicted, so a session-replay backward load is now genuinely available --
    // this is the exact state that used to stay stuck at `false`, leaving backward-scroll
    // loading dead because MemesList's `startReached={() => { if (hasMoreBackward) loadBackward() }}`
    // gate never fired.
    expect(result.current.hasMoreBackward).toBe(true)
  })

  it('replays a remembered cursor on loadBackward after eviction', async () => {
    const fetchPage = vi.fn<FetchPageFn>()
      .mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p1', 2), nextCursor: 'c2', hasNext: true })
      .mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k', maxPages: 2 }))

    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['p0-0', 'p0-1'])) // auto-loaded, cursor undefined
    await act(async () => { await result.current.loadForward() }) // p1, cursor c1
    await act(async () => { await result.current.loadForward() }) // p2, cursor c2 -- p0 evicted

    fetchPage.mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
    const firstItemIndexBefore = result.current.firstItemIndex
    await act(async () => { await result.current.loadBackward() })

    expect(fetchPage).toHaveBeenLastCalledWith(undefined, 'forward') // p0's own cursor was undefined
    expect(result.current.items[0].id).toBe('p0-0')
    expect(result.current.firstItemIndex).toBe(firstItemIndexBefore - 2)
  })

  it('rewinds the forward frontier when loadBackward evicts the newest page, so a later loadForward re-fetches it instead of skipping it', async () => {
    const fetchPage = vi.fn<FetchPageFn>()
      .mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true }) // auto-load p0
      .mockResolvedValueOnce({ items: makeMemes('p1', 2), nextCursor: 'c2', hasNext: true }) // p1
      .mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true }) // p2 -- evicts p0 from the front
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k', maxPages: 2 }))

    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['p0-0', 'p0-1'])) // auto-loaded
    await act(async () => { await result.current.loadForward() }) // p1, cursor c1
    const stateAfterP1 = {
      items: result.current.items.map(m => m.id),
      firstItemIndex: result.current.firstItemIndex,
    }
    await act(async () => { await result.current.loadForward() }) // p2, cursor c2 -- evicts p0 from the front
    expect(result.current.hasMoreForward).toBe(true)

    // Replaying p0 on loadBackward pushes the window back over maxPages, evicting p2 from the back.
    fetchPage.mockResolvedValueOnce({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
    await act(async () => { await result.current.loadBackward() })

    // The window after this round-trip must be byte-for-byte identical to the state right before
    // the eviction (right after the p1 forward load) -- no skipped content, no stale replay.
    expect(result.current.items.map(m => m.id)).toEqual(stateAfterP1.items)
    expect(result.current.firstItemIndex).toBe(stateAfterP1.firstItemIndex)
    expect(result.current.hasMoreForward).toBe(true)

    // loadForward again must re-fetch the evicted p2 page using ITS OWN cursor (c2, the cursor
    // that originally fetched it) -- not silently skip past it using some later/stale cursor.
    fetchPage.mockResolvedValueOnce({ items: makeMemes('p2', 2), nextCursor: 'c3', hasNext: true })
    await act(async () => { await result.current.loadForward() })

    expect(fetchPage).toHaveBeenLastCalledWith('c2', 'forward')
    expect(result.current.items.map(m => m.id)).toEqual(['p1-0', 'p1-1', 'p2-0', 'p2-1'])
    expect(result.current.hasMoreForward).toBe(true)
  })

  it('does not attempt loadBackward with no history and supportsColdBackward unset', async () => {
    const fetchPage = vi.fn<FetchPageFn>().mockResolvedValue({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
    const { result } = renderHook(() => useWindowedPagination({ fetchPage, resetKey: 'k' }))
    await waitFor(() => expect(result.current.items).toHaveLength(2))

    expect(result.current.hasMoreBackward).toBe(false)
    const callCountBefore = fetchPage.mock.calls.length
    await act(async () => { await result.current.loadBackward() })
    expect(fetchPage.mock.calls.length).toBe(callCountBefore)
  })

  it('supportsColdBackward calls fetchPage with direction "backward" using the anchor cursor', async () => {
    const fetchPage = vi.fn<FetchPageFn>()
      .mockResolvedValueOnce({ items: [], hasNext: false }) // the auto forward load on mount
      .mockResolvedValueOnce({ items: makeMemes('before', 2), previousCursor: undefined }) // the backward fetch
    const { result } = renderHook(() =>
      useWindowedPagination({ fetchPage, resetKey: 'k', initialCursor: 'deep-link-cursor', supportsColdBackward: true })
    )
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1))
    expect(result.current.hasMoreBackward).toBe(true)

    await act(async () => { await result.current.loadBackward() })

    expect(fetchPage).toHaveBeenLastCalledWith('deep-link-cursor', 'backward')
    expect(result.current.items.map(m => m.id)).toEqual(['before-0', 'before-1'])
  })

  it('does not call fetchPage for a cold-backward load when there is no real anchor (no initialCursor, no prior pages)', async () => {
    // The very first page ever fetched has cursor `undefined` (no initialCursor was given), so
    // once it loads, pagesRef.current[0]?.cursor ?? initialCursorRef.current is also `undefined`
    // -- there's no real anchor to page backward from, i.e. we're already at the true beginning.
    // Previously this still issued a backward fetch with anchor=undefined, which the real backend
    // (and the mock repo in the backend test) would interpret as "no cursor filter" and return
    // the tail of the corpus instead of nothing.
    const fetchPage = vi.fn<FetchPageFn>().mockResolvedValue({ items: makeMemes('p0', 2), nextCursor: 'c1', hasNext: true })
    const { result } = renderHook(() =>
      useWindowedPagination({ fetchPage, resetKey: 'k', supportsColdBackward: true })
    )
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1)) // the auto forward load on mount
    expect(result.current.hasMoreBackward).toBe(true) // optimistic, per supportsColdBackward

    await act(async () => { await result.current.loadBackward() })

    expect(fetchPage).toHaveBeenCalledTimes(1) // no additional (backward) call was made
    expect(result.current.hasMoreBackward).toBe(false)
  })

  it('marks coldBackward exhausted once a backward fetch returns no items', async () => {
    const fetchPage = vi.fn<FetchPageFn>().mockResolvedValue({ items: [] })
    const { result } = renderHook(() =>
      useWindowedPagination({ fetchPage, resetKey: 'k', initialCursor: 'x', supportsColdBackward: true })
    )
    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1)) // the auto forward load on mount

    await act(async () => { await result.current.loadBackward() })

    expect(result.current.hasMoreBackward).toBe(false)
    expect(result.current.items).toHaveLength(0)
  })

  it('resets all state when resetKey changes', async () => {
    const fetchPage = vi.fn<FetchPageFn>().mockResolvedValue({ items: makeMemes('a', 1), hasNext: false })
    const { result, rerender } = renderHook(
      ({ key }) => useWindowedPagination({ fetchPage, resetKey: key }),
      { initialProps: { key: 'first' } }
    )
    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['a-0']))

    fetchPage.mockResolvedValue({ items: makeMemes('b', 1), hasNext: false })
    rerender({ key: 'second' })
    await waitFor(() => expect(result.current.items.map(m => m.id)).toEqual(['b-0']))
  })
})

describe('cursorForVirtualIndex', () => {
  const pages: Page[] = [
    { cursor: undefined, items: makeMemes('p0', 2) },
    { cursor: 'c1', items: makeMemes('p1', 3) },
  ]

  it('returns the owning page cursor for an index within the second page', () => {
    // firstItemIndex=100 -> p0 occupies [100,101], p1 occupies [102,103,104]
    expect(cursorForVirtualIndex(pages, 100, 103)).toBe('c1')
  })

  it('returns the first page cursor for an index within the first page', () => {
    expect(cursorForVirtualIndex(pages, 100, 100)).toBeUndefined()
  })

  it('clamps to the first page cursor for an out-of-range low index', () => {
    expect(cursorForVirtualIndex(pages, 100, 50)).toBeUndefined()
  })
})
