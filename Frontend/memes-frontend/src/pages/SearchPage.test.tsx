import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SearchPage from './SearchPage'
import { makeMockApi } from '../test/mockApi'
import type { MemesApi } from '../api/MemesApi'
import { readRecentSearches } from '../home/recentSearches'

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

  it('remembers a search only when it is committed with Enter', async () => {
    localStorage.clear()
    renderPage(makeMockApi())
    const input = screen.getByPlaceholderText('Search memes...')
    fireEvent.change(input, { target: { value: 'pizza' } })
    await waitFor(() => expect(input).toHaveValue('pizza'))
    expect(readRecentSearches()).toEqual([])
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(readRecentSearches()).toEqual(['pizza'])
  })

  it('does not remember an Enter on a blank box', () => {
    localStorage.clear()
    renderPage(makeMockApi())
    fireEvent.keyDown(screen.getByPlaceholderText('Search memes...'), { key: 'Enter' })
    expect(readRecentSearches()).toEqual([])
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

  it("offers 'I didn't find it' when the search has an id, and reports that id", async () => {
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue({ items: [], hasNext: false, searchId: 's1' }) })
    renderPage(api, '?q=cats')
    fireEvent.click(await screen.findByRole('button', { name: "I didn't find it" }))
    await waitFor(() => expect(api.reportNotFound).toHaveBeenCalledWith('s1'))
  })

  it("has no 'I didn't find it' button when the response carries no search id", async () => {
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue({ items: [], hasNext: false }) })
    renderPage(api, '?q=cats')
    await waitFor(() => expect(api.searchMemes).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: "I didn't find it" })).not.toBeInTheDocument()
  })
})
