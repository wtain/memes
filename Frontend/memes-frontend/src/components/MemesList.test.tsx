import { render, screen, waitFor } from '@testing-library/react'
import { MemesList } from './MemesList'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const mockObserver = { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() }
  global.IntersectionObserver = vi.fn(() => mockObserver) as unknown as typeof IntersectionObserver
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

  it('calls iterateDuplicates instead of searchMemes when listDuplicates is true', async () => {
    const api = makeMockApi()
    render(<MemesList memesApi={api} listDuplicates />)
    await waitFor(() => {
      expect(api.iterateDuplicates).toHaveBeenCalled()
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
})
