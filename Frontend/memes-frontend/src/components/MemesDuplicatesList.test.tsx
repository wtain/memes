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
} = {}

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: {
    data: unknown[]
    itemContent: (index: number, item: unknown) => React.ReactNode
    rangeChanged?: (range: { startIndex: number }) => void
    endReached?: () => void
  }) => {
    capturedProps.rangeChanged = props.rangeChanged
    capturedProps.endReached = props.endReached
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
