import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SearchPage from './SearchPage'
import type { MemesApi } from '../api/MemesApi'
import type { Meme } from '../types/generated/all'

const mockMeme: Meme = { id: '1', imageUrl: '/img.jpg', text: [], tags: [], excluded: false }

function makeMockApi(overrides: Partial<MemesApi> = {}): MemesApi {
  return {
    searchMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateUntaggedMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateDuplicates: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getImageUrl: vi.fn().mockReturnValue('http://example.com/img.jpg'),
    listConcepts: vi.fn().mockResolvedValue([]),
    getTopImagesForConcept: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getTopConceptsForImage: vi.fn().mockResolvedValue([]),
    getMeme: vi.fn().mockResolvedValue(mockMeme),
    getConcept: vi.fn().mockResolvedValue({ id: 1, name: 'test' }),
    markImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    unmarkImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    getImageIsExcluded: vi.fn().mockResolvedValue(false),
    ...overrides,
  } as MemesApi
}

function renderPage(api: MemesApi, search = '') {
  return render(
    <MemoryRouter initialEntries={[`/search${search}`]}>
      <SearchPage memesApi={api} />
    </MemoryRouter>
  )
}

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  const mockObserver = { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() }
  global.IntersectionObserver = vi.fn(() => mockObserver) as unknown as typeof IntersectionObserver
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('SearchPage', () => {
  it('renders the search input', () => {
    renderPage(makeMockApi())
    expect(screen.getByPlaceholderText('Search memes...')).toBeInTheDocument()
  })

  it('pre-fills input from the q URL param', () => {
    renderPage(makeMockApi(), '?q=cats')
    expect(screen.getByPlaceholderText('Search memes...')).toHaveValue('cats')
  })

  it('updates input value when user types', async () => {
    renderPage(makeMockApi())
    const input = screen.getByPlaceholderText('Search memes...')
    fireEvent.change(input, { target: { value: 'dogs' } })
    await waitFor(() => expect(input).toHaveValue('dogs'))
  })

  it('passes the initial URL query to MemesList search', async () => {
    const api = makeMockApi()
    renderPage(api, '?q=cats')
    // useDebounce initialises with the current value, so searchMemes is called immediately
    await waitFor(() => {
      expect(api.searchMemes).toHaveBeenCalledWith(
        expect.objectContaining({ query: 'cats' })
      )
    })
  })

  it('renders the facet sidebar', () => {
    renderPage(makeMockApi())
    expect(screen.getByRole('button', { name: /Filters/ })).toBeInTheDocument()
  })

  it('calls searchMemes with tag filters from URL', async () => {
    const api = makeMockApi()
    renderPage(api, '?category=cats')
    await waitFor(() => {
      expect(api.searchMemes).toHaveBeenCalledWith(
        expect.objectContaining({
          tags: expect.arrayContaining([{ category: 'category', name: 'cats' }]),
        })
      )
    })
  })
})
