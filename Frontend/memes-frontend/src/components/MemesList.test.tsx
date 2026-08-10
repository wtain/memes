import { render, screen, waitFor } from '@testing-library/react'
import { MemesList } from './MemesList'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'

vi.mock('react-virtuoso', () => ({
  Virtuoso: (props: { data: unknown[]; itemContent: (index: number, item: unknown) => React.ReactNode; endReached?: (index: number) => void; startReached?: (index: number) => void }) => (
    <div>
      {props.data.map((item, i) => (
        <div key={i}>{props.itemContent(i, item)}</div>
      ))}
    </div>
  ),
}))

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
      expect(screen.getByRole('img', { name: DEFAULT_MOCK_MEME.id })).toBeInTheDocument()
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
    const rowWrappers = container.querySelectorAll('.grid.grid-cols-1.md\\:grid-cols-6')
    expect(rowWrappers).toHaveLength(2)
  })
})
