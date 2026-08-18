import { act, render, screen, waitFor } from '@testing-library/react'
import { MemesDuplicatesList } from './MemesDuplicatesList'
import { makeMockApi } from '../test/mockApi'
import type { Meme } from '../types/generated/all'

const CURSOR_DEBOUNCE_MS = 300

// Captures the callback props Virtuoso is given so tests can invoke them directly (the mock
// below never calls them itself -- it only renders `data`/`itemContent`).
const capturedProps: {
  rangeChanged?: (range: { startIndex: number }) => void
  endReached?: () => void
  startReached?: () => void
  firstItemIndex?: number
} = {}

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => React.ReactNode
    rangeChanged?: (range: { startIndex: number }) => void
    endReached?: () => void
    startReached?: () => void
    firstItemIndex?: number
  }) => {
    capturedProps.rangeChanged = props.rangeChanged
    capturedProps.endReached = props.endReached
    capturedProps.startReached = props.startReached
    capturedProps.firstItemIndex = props.firstItemIndex
    return (
      <div>
        {props.data.map((item, i) => (
          <div key={i}>{props.itemContent(i, item)}</div>
        ))}
      </div>
    )
  },
}))

function clusterMeme(id: string, clusterId: number): Meme {
  return { id, imageUrl: `/images/${id}.jpg`, text: [], tags: [], clusterId }
}

beforeEach(() => {
  // A fresh (no initialCursor) mount scrolls to top -- see the effect in
  // MemesDuplicatesList.tsx. jsdom doesn't implement window.scrollTo, so this avoids noisy
  // "Not implemented" stderr output, matching the existing pattern in MemesList.test.tsx.
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MemesDuplicatesList', () => {
  it('calls iterateDuplicates on mount with no cursor by default', async () => {
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalledWith(40, undefined, 0.2)
    })
  })

  it('starts from the URL-provided initialCursor', async () => {
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} initialCursor="deep-link" />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalledWith(40, "deep-link", 0.2)
    })
  })

  it('scrolls to top on a fresh open with no cursor (regression)', async () => {
    // Regression test: without this, a fresh open of this page (e.g. after navigating away
    // and back) left the browser's window scroll position wherever it was on the previous
    // page. Virtuoso's useWindowScroll mode reconciled its freshly-loaded content against that
    // leftover offset, which showed up as rapid scroll-jumping and a wrong cursor getting
    // written to the URL almost immediately instead of starting from the beginning.
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => expect(api.iterateDuplicates).toHaveBeenCalled())
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 })
  })

  it('does not force-scroll to top when resuming from a URL cursor', async () => {
    // The whole point of initialCursor is to preserve scroll position across a genuine
    // return-to-this-page navigation -- resuming must not snap back to the top.
    const api = makeMockApi()
    render(<MemesDuplicatesList memesApi={api} initialCursor="deep-link" />)
    await waitFor(() => expect(api.iterateDuplicates).toHaveBeenCalled())
    expect(window.scrollTo).not.toHaveBeenCalled()
  })

  it('groups same-cluster members into one row and renders them together', async () => {
    const api = makeMockApi({
      iterateDuplicates: vi.fn().mockResolvedValue({
        items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 2)],
        hasNext: false,
      }),
    })
    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => {
      expect(screen.getByRole('img', { name: 'a' })).toBeInTheDocument()
      expect(screen.getByRole('img', { name: 'b' })).toBeInTheDocument()
      expect(screen.getByRole('img', { name: 'c' })).toBeInTheDocument()
    })
  })

  it('shows "Nothing to show" when there are no clusters', async () => {
    render(<MemesDuplicatesList memesApi={makeMockApi()} />)
    await waitFor(() => {
      expect(screen.getByText('Nothing to show')).toBeInTheDocument()
    })
  })

  it('does not over-count a cluster that straddles an evicted page boundary (regression)', async () => {
    // Cluster 2 straddles pages 1 and 2 (member 'b' in page 1, member 'c' in page 2). When page 1
    // is evicted (on the 5th page load, with the default maxPages=4), cluster 2's row survives --
    // it just loses member 'b' -- because 'c' keeps it alive in the window. Only cluster 1's row
    // actually disappears. The row-index shift must therefore be 1, not 2 (the raw count of
    // distinct clusters page 1 happened to touch, which over-counts the surviving straddler).
    const iterateDuplicates = vi.fn()
      .mockResolvedValueOnce({ items: [clusterMeme('a', 1), clusterMeme('b', 2)], nextCursor: 'c1', hasNext: true })
      .mockResolvedValueOnce({ items: [clusterMeme('c', 2), clusterMeme('d', 3)], nextCursor: 'c2', hasNext: true })
      .mockResolvedValueOnce({ items: [clusterMeme('e', 4), clusterMeme('f', 5)], nextCursor: 'c3', hasNext: true })
      .mockResolvedValueOnce({ items: [clusterMeme('g', 6), clusterMeme('h', 7)], nextCursor: 'c4', hasNext: true })
      .mockResolvedValueOnce({ items: [clusterMeme('i', 8), clusterMeme('j', 9)], hasNext: false }) // evicts page 1
    const api = makeMockApi({ iterateDuplicates })

    render(<MemesDuplicatesList memesApi={api} />)
    await waitFor(() => expect(screen.getByRole('img', { name: 'b' })).toBeInTheDocument()) // mount

    for (const id of ['d', 'f', 'h', 'j']) {
      await act(async () => { capturedProps.endReached?.() })
      await waitFor(() => expect(screen.getByRole('img', { name: id })).toBeInTheDocument())
    }
    expect(iterateDuplicates).toHaveBeenCalledTimes(5)

    expect(capturedProps.firstItemIndex).toBe(10_001)
  })

  it('does not over-count a cluster that straddles a cold-backward prepend boundary (regression)', async () => {
    // Mirror of the eviction case above, for the opposite direction: cluster 2 straddles the
    // mount page (member 'c') and the cold-backward-prepended page before it (member 'b'). The
    // prepend should only count cluster 1's row as newly added -- cluster 2 already had a row
    // before the prepend, just with fewer members.
    const iterateDuplicates = vi.fn()
      .mockResolvedValueOnce({ items: [clusterMeme('c', 2), clusterMeme('d', 3)], hasNext: false }) // mount, cursor 'mid'
      .mockResolvedValueOnce({ items: [clusterMeme('x', 1), clusterMeme('b', 2)], hasNext: false }) // cold-backward prepend
    const api = makeMockApi({ iterateDuplicates })

    render(<MemesDuplicatesList memesApi={api} initialCursor="mid" />)
    await waitFor(() => expect(screen.getByRole('img', { name: 'd' })).toBeInTheDocument()) // mount

    await act(async () => { capturedProps.startReached?.() })
    await waitFor(() => expect(screen.getByRole('img', { name: 'x' })).toBeInTheDocument())
    expect(iterateDuplicates).toHaveBeenCalledTimes(2)

    expect(capturedProps.firstItemIndex).toBe(9_999)
  })

  describe('onCursorChange row-to-cursor mapping', () => {
    afterEach(() => {
      vi.useRealTimers()
    })

    it('computes the debounced cursor from the row\'s actual item position, not its raw row index (regression)', async () => {
      vi.useFakeTimers()
      const api = makeMockApi({
        iterateDuplicates: vi.fn()
          .mockResolvedValueOnce({
            items: [clusterMeme('a', 1), clusterMeme('b', 1), clusterMeme('c', 1)], // row 0, 3 members
            nextCursor: 'cursor-2',
            hasNext: true,
          })
          .mockResolvedValueOnce({
            items: [clusterMeme('d', 2), clusterMeme('e', 2)], // row 1, item-space offset 3
            hasNext: false,
          }),
      })
      const onCursorChange = vi.fn()
      render(<MemesDuplicatesList memesApi={api} onCursorChange={onCursorChange} />)

      await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // flush the initial (row 0) page load
      expect(api.iterateDuplicates).toHaveBeenCalledTimes(1)

      // Load the second page so row 1 (cluster 2) lands in a *different* page than row 0, giving
      // it a distinct cursor -- without this, both rows would resolve to the same single-page
      // cursor and the test couldn't distinguish correct (item-space) from buggy (row-space) math.
      await act(async () => {
        capturedProps.endReached?.()
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(api.iterateDuplicates).toHaveBeenCalledTimes(2)

      // Both pages loaded via forward fetches with nothing evicted, so rowFirstItemIndex and the
      // hook's own firstItemIndex are still at their shared starting value (10_000) -- row index 1
      // is therefore virtual index 10_001.
      await act(async () => {
        capturedProps.rangeChanged?.({ startIndex: 10_001 })
        await vi.advanceTimersByTimeAsync(CURSOR_DEBOUNCE_MS)
      })

      // Row-space math (the bug) would resolve virtual index 10_001 directly against item-space
      // page boundaries and land inside page 1 (items 0-2, cursor undefined). Item-space math
      // (the fix) converts row 1 to its true item offset (3), landing in page 2 -- 'cursor-2'.
      expect(onCursorChange).toHaveBeenCalledWith('cursor-2')
    })

    it('does not call onCursorChange after unmount if the debounce timer was still pending', async () => {
      vi.useFakeTimers()
      const api = makeMockApi({
        iterateDuplicates: vi.fn().mockResolvedValue({
          items: [clusterMeme('a', 1)],
          hasNext: false,
        }),
      })
      const onCursorChange = vi.fn()
      const { unmount } = render(<MemesDuplicatesList memesApi={api} onCursorChange={onCursorChange} />)

      await act(async () => { await vi.advanceTimersByTimeAsync(0) }) // flush the initial page load

      act(() => { capturedProps.rangeChanged?.({ startIndex: 10_000 }) }) // starts the debounce timer
      unmount()

      await act(async () => { await vi.advanceTimersByTimeAsync(CURSOR_DEBOUNCE_MS) })

      expect(onCursorChange).not.toHaveBeenCalled()
    })
  })
})
