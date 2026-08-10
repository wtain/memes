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
