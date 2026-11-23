import { act, render, screen, waitFor, within } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { MemesList } from './MemesList'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'
import { getCardImg, findCardImg } from '../test/cardQueries'

// Captures the callback props Virtuoso is given so tests can invoke them directly (the mock
// below never calls them itself -- it only renders `data`/`itemContent`), same pattern used in
// MemesDuplicatesList.test.tsx.
const capturedProps: {
  startReached?: () => void
  endReached?: () => void
} = {}

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: { data: unknown[]; itemContent: (index: number, item: unknown) => React.ReactNode; endReached?: () => void; startReached?: () => void }) => {
    capturedProps.startReached = props.startReached
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

beforeEach(() => {
  // MemesList scrolls to top on every resetKey change (see FIX 5) -- jsdom doesn't implement
  // window.scrollTo, so this avoids noisy "Not implemented" stderr output across every test here,
  // matching the existing pattern in SearchPage.test.tsx.
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MemesList', () => {
  it('calls searchMemes on mount', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} />)
    await waitFor(() => {
      expect(api.searchMemes).toHaveBeenCalledWith({
        cursor: undefined,
        limit: 36,
        query: undefined,
        tags: [],
      })
    })
  })

  it('shows "Nothing to show" when response is empty', async () => {
    render(<MemesList memesApi={makeMockApi()} />)
    await waitFor(() => {
      expect(screen.getByText('Nothing to show')).toBeInTheDocument()
    })
  })

  it('shows six skeleton cards and one Loading status until the first page arrives', async () => {
    let resolve!: (v: unknown) => void
    const api = makeMockApi({
      searchMemes: vi.fn().mockReturnValue(new Promise(r => { resolve = r })),
    })
    const { container } = render(<MemesList memesApi={api} />)
    await waitFor(() => expect(screen.getAllByTestId('meme-card-skeleton')).toHaveLength(6))
    expect(screen.getByRole('status')).toHaveTextContent('Loading...')
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull()
    expect(screen.queryByText('Nothing to show')).not.toBeInTheDocument()
    await act(async () => { resolve({ items: [DEFAULT_MOCK_MEME], facets: [], hasNext: false }) })
    await waitFor(() => expect(screen.queryAllByTestId('meme-card-skeleton')).toHaveLength(0))
  })

  it('renders the empty result through EmptyState', async () => {
    render(<MemesList memesApi={makeMockApi()} />)
    expect(await screen.findByText('Nothing to show')).toBeInTheDocument()
    expect(screen.getByRole('status')).toBeInTheDocument()
  })

  it('exposes each card as a link to its meme page', async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: [DEFAULT_MOCK_MEME], facets: [], hasNext: false }),
    })
    const { container } = render(<MemesList memesApi={api} />)
    await waitFor(() => expect(container.querySelector('a[href^="/memes/"]')).not.toBeNull())
  })

  it('uses a two-column grid on phones', async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: [DEFAULT_MOCK_MEME], facets: [], hasNext: false }),
    })
    const { container } = render(<MemesList memesApi={api} />)
    await waitFor(() => expect(container.querySelector('.grid-cols-2.sm\\:grid-cols-3.md\\:grid-cols-6')).not.toBeNull())
  })

  it('renders a card for each returned meme', async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({
        items: [DEFAULT_MOCK_MEME],
        facets: [],
        hasNext: false,
      }),
    })
    render(<MemesList memesApi={api} />)
    await waitFor(() => {
      expect(getCardImg(DEFAULT_MOCK_MEME.id)).toBeInTheDocument()
    })
  })

  it('calls iterateUntaggedMemes instead of searchMemes when listUntagged is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listUntagged />)
    await waitFor(() => {
      expect(api.iterateUntaggedMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })

  it('calls iterateNoOcrMemes instead of searchMemes when listNoOcr is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listNoOcr />)
    await waitFor(() => {
      expect(api.iterateNoOcrMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })

  it('calls iterateFlaggedMemes instead of searchMemes when listFlagged is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listFlagged />)
    await waitFor(() => {
      expect(api.iterateFlaggedMemes).toHaveBeenCalled()
      expect(api.searchMemes).not.toHaveBeenCalled()
    })
  })

  it('offers Add to comics on search results but not on the flagged list', async () => {
    const items = { items: [DEFAULT_MOCK_MEME], facets: [], hasNext: false }
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue(items),
      iterateFlaggedMemes: vi.fn().mockResolvedValue(items),
    })
    const { unmount } = render(<MemesList memesApi={api} />)
    expect(await screen.findByRole('button', { name: 'Add to comics' })).toBeInTheDocument()
    unmount()
    render(<MemesList memesApi={api} listFlagged />)
    await findCardImg(DEFAULT_MOCK_MEME.id)
    expect(screen.queryByRole('button', { name: 'Add to comics' })).not.toBeInTheDocument()
  })

  it('invokes onFacetsChanged with facets from the response', async () => {
    const onFacetsChanged = vi.fn()
    const facets = [{ name: 'category', buckets: [{ value: 'cats', count: 5 }] }]
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: [], facets, hasNext: false }),
    })
    render(<MemesList memesApi={api} onFacetsChanged={onFacetsChanged} />)
    await waitFor(() => {
      expect(onFacetsChanged).toHaveBeenCalledWith(facets)
    })
  })

  it('applies tagFilters as tags in the search request', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} tagFilters={{ category: ['cats', 'dogs'] }} />)
    await waitFor(() => {
      expect(api.searchMemes).toHaveBeenCalledWith(
        expect.objectContaining({
          tags: expect.arrayContaining([
            { category: 'category', name: 'cats' },
            { category: 'category', name: 'dogs' },
          ]),
        })
      )
    })
  })

  it('re-fetches from scratch when the filter changes', async () => {
    const api = makeMockApi()
    const { rerender } = render(<MemesList memesApi={api} filter="first query" />)
    await waitFor(() => expect(api.searchMemes).toHaveBeenCalledWith(expect.objectContaining({ query: 'first query' })))

    rerender(<MemesList memesApi={api} filter="second query" />)
    await waitFor(() => expect(api.searchMemes).toHaveBeenCalledWith(expect.objectContaining({ query: 'second query' })))
  })

  it('chunks items into rows of up to 6 for the grid layout', async () => {
    const items = Array.from({ length: 8 }, (_, i) => ({ ...DEFAULT_MOCK_MEME, id: `m${i}` }))
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items, facets: [], hasNext: false }),
    })
    const { container } = render(<MemesList memesApi={api} />)
    await waitFor(() => {
      expect(screen.getAllByRole('img')).toHaveLength(8)
    })
    // 8 items at 6 columns -> one full row of 6, one partial row of 2.
    const rowWrappers = container.querySelectorAll('.grid.grid-cols-2.sm\\:grid-cols-3.md\\:grid-cols-6')
    expect(rowWrappers).toHaveLength(2)
    expect(within(rowWrappers[0] as HTMLElement).getAllByRole('img')).toHaveLength(6)
    expect(within(rowWrappers[1] as HTMLElement).getAllByRole('img')).toHaveLength(2)
  })

  it('re-fetches an earlier cursor via startReached once forward loading has evicted a page (regression: hasMoreBackward used to stay stuck at false)', async () => {
    // Six sequential pages: the mount load plus five endReached-triggered forward loads. With
    // the hook's default maxPages=4, the 5th page pushed (after 4 endReached calls) evicts the
    // oldest page, and the 6th page pushed (after a 5th endReached call) evicts the next-oldest
    // -- leaving the replay log pointing at 'c1' (the cursor that fetched the second page) as the
    // cursor startReached should now be able to re-fetch.
    const makePage = (id: string) => ({
      items: [{ ...DEFAULT_MOCK_MEME, id }],
      nextCursor: id,
      hasNext: true,
    })
    const iterateUntaggedMemes = vi.fn()
      .mockResolvedValueOnce(makePage('c1')) // mount, cursor undefined -> nextCursor c1
      .mockResolvedValueOnce(makePage('c2')) // endReached #1, cursor c1 -> nextCursor c2
      .mockResolvedValueOnce(makePage('c3')) // endReached #2, cursor c2 -> nextCursor c3
      .mockResolvedValueOnce(makePage('c4')) // endReached #3, cursor c3 -> nextCursor c4
      .mockResolvedValueOnce(makePage('c5')) // endReached #4, cursor c4 -> nextCursor c5 -- evicts page 1 (cursor undefined)
      .mockResolvedValueOnce(makePage('c6')) // endReached #5, cursor c5 -> nextCursor c6 -- evicts page 2 (cursor c1)
      .mockResolvedValue(makePage('replay')) // fallback for the loadBackward replay call and beyond
    const api = makeMockApi({ iterateUntaggedMemes })

    render(<MemesList memesApi={api} listUntagged />)
    await waitFor(() => expect(getCardImg('c1')).toBeInTheDocument()) // mount

    // Wait for each page's own item to actually render before triggering the next endReached --
    // confirms the previous loadForward's state update (and loadingRef reset) has fully landed,
    // not just that the mock function was called.
    for (const id of ['c2', 'c3', 'c4', 'c5', 'c6']) {
      await act(async () => { capturedProps.endReached?.() })
      await waitFor(() => expect(getCardImg(id)).toBeInTheDocument())
    }
    expect(iterateUntaggedMemes).toHaveBeenCalledTimes(6)

    const callsBeforeBackward = iterateUntaggedMemes.mock.calls.length
    await act(async () => { capturedProps.startReached?.() })

    // The component's own `startReached={() => { if (hasMoreBackward) loadBackward() }}` gate is
    // the exact thing FIX 1 repairs: before the fix, hasMoreBackward never became true after a
    // front-eviction (only after a loadBackward-side eviction), so this call was silently a no-op
    // and backward-scroll loading was dead on every MemesList page.
    await waitFor(() => expect(iterateUntaggedMemes.mock.calls.length).toBe(callsBeforeBackward + 1))
    expect(iterateUntaggedMemes).toHaveBeenLastCalledWith(21, 'c1')
  })

  it('shows an error state with Try again when the first page fails', async () => {
    const searchMemes = vi.fn().mockRejectedValueOnce(new Error('boom'))
    const api = makeMockApi({ searchMemes })
    render(<MemesList memesApi={api} />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not load memes')
    expect(screen.queryByText('Nothing to show')).not.toBeInTheDocument()
    expect(searchMemes).toHaveBeenCalledTimes(1)

    searchMemes.mockResolvedValueOnce({ items: [DEFAULT_MOCK_MEME], facets: [], hasNext: false })
    await act(async () => { screen.getByRole('button', { name: 'Try again' }).click() })
    await waitFor(() => expect(getCardImg(DEFAULT_MOCK_MEME.id)).toBeInTheDocument())
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps loaded cards and shows an inline alert when a later page fails', async () => {
    const searchMemes = vi.fn()
      .mockResolvedValueOnce({ items: [{ ...DEFAULT_MOCK_MEME, id: 'p1' }], nextCursor: 'cur1', hasNext: true })
      .mockRejectedValueOnce(new Error('boom'))
    const api = makeMockApi({ searchMemes })
    render(<MemesList memesApi={api} />)
    await waitFor(() => expect(getCardImg('p1')).toBeInTheDocument())

    await act(async () => { capturedProps.endReached?.() })
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Try again')
    expect(getCardImg('p1')).toBeInTheDocument()
    expect(searchMemes).toHaveBeenCalledTimes(2)

    searchMemes.mockResolvedValueOnce({ items: [{ ...DEFAULT_MOCK_MEME, id: 'p2' }], hasNext: false })
    await act(async () => { screen.getByRole('button', { name: 'Try again' }).click() })
    await waitFor(() => expect(getCardImg('p2')).toBeInTheDocument())
    expect(searchMemes).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 'cur1' }))
  })

  it('says no memes match for a query that found nothing', async () => {
    render(<MemesList memesApi={makeMockApi()} filter="zzzz" />)
    expect(await screen.findByText('No memes match')).toBeInTheDocument()
    expect(screen.getByText('Try fewer words or clear a filter.')).toBeInTheDocument()
  })

  it('says no memes match when tag filters found nothing', async () => {
    render(<MemesList memesApi={makeMockApi()} tagFilters={{ topic: ['cats'] }} />)
    expect(await screen.findByText('No memes match')).toBeInTheDocument()
    expect(screen.getByText('Try fewer words or clear a filter.')).toBeInTheDocument()
  })

  it('keeps "Nothing to show" for an unfiltered empty list', async () => {
    render(<MemesList memesApi={makeMockApi()} tagFilters={{ topic: [] }} />)
    expect(await screen.findByText('Nothing to show')).toBeInTheDocument()
    expect(screen.queryByText('No memes match')).not.toBeInTheDocument()
  })
  describe('quick-look arrows', () => {
    const memes = Array.from({ length: 5 }, (_, i) => ({ ...DEFAULT_MOCK_MEME, id: `m${i}` }))
    const renderList = (api: ReturnType<typeof makeMockApi>) =>
      render(<MemoryRouter><MemesList memesApi={api} /></MemoryRouter>)
    const open = async (id: string) => {
      fireEvent.click(await findCardImg(id))
      await screen.findByAltText(id)
    }

    it('ArrowRight and ArrowLeft step through the list inside the open modal', async () => {
      const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue({ items: memes, facets: [], hasNext: false }) })
      renderList(api)
      await open('m1')
      fireEvent.keyDown(document.body, { key: 'ArrowRight' })
      expect(await screen.findByAltText('m2')).toBeInTheDocument()
      fireEvent.keyDown(document.body, { key: 'ArrowLeft' })
      await screen.findByAltText('m1')
      fireEvent.keyDown(document.body, { key: 'ArrowLeft' })
      expect(await screen.findByAltText('m0')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Previous meme' })).toBeDisabled()
      await act(async () => {}) // settle the card and detail fetches before the mocks are restored
    })

    it('requests the next page when stepping to one of the last two items while more exist', async () => {
      const searchMemes = vi.fn().mockResolvedValue({ items: memes, facets: [], hasNext: true, nextCursor: 'c1' })
      renderList(makeMockApi({ searchMemes }))
      await open('m0')
      fireEvent.keyDown(document.body, { key: 'ArrowRight' })
      await screen.findByAltText('m1')
      fireEvent.keyDown(document.body, { key: 'ArrowRight' })
      await screen.findByAltText('m2')
      expect(searchMemes).toHaveBeenCalledTimes(1)
      fireEvent.keyDown(document.body, { key: 'ArrowRight' })
      await screen.findByAltText('m3')
      await waitFor(() => expect(searchMemes).toHaveBeenCalledTimes(2))
      expect(searchMemes.mock.calls[1][0]).toEqual(expect.objectContaining({ cursor: 'c1' }))
    })

    it('shows no arrows when the opened meme is not in the list', async () => {
      const searchMemes = vi.fn().mockResolvedValue({ items: memes, facets: [], hasNext: false })
      const { rerender } = renderList(makeMockApi({ searchMemes }))
      await open('m1')
      expect(screen.getByRole('button', { name: 'Next meme' })).toBeInTheDocument()
      // A different filter replaces the list; the opened meme is no longer part of it.
      searchMemes.mockResolvedValue({ items: [{ ...DEFAULT_MOCK_MEME, id: 'other' }], facets: [], hasNext: false })
      rerender(<MemoryRouter><MemesList memesApi={makeMockApi({ searchMemes })} filter="zz" /></MemoryRouter>)
      await findCardImg('other')
      expect(screen.getByAltText('m1')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Next meme' })).not.toBeInTheDocument()
    })
  })
})
