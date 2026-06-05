import { render, screen, act, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { ConceptDetails } from './ConceptDetails'
import { makeMockApi } from '../test/mockApi'
import type { Concept } from '../types/generated/all'

const mockConcept: Concept = { id: 1, name: 'test-concept' }

describe('ConceptDetails', () => {
  it('calls getTopImagesForConcept once per mount (production behaviour)', async () => {
    const api = makeMockApi()
    render(
      <MemoryRouter>
        <ConceptDetails concept={mockConcept} memesApi={api} />
      </MemoryRouter>
    )
    await act(async () => {})
    expect(api.getTopImagesForConcept).toHaveBeenCalledTimes(1)
    expect(api.getTopImagesForConcept).toHaveBeenCalledWith(1)
  })

  it('shows a loading indicator initially, then hides it after fetch resolves', async () => {
    const api = makeMockApi()
    render(
      <MemoryRouter>
        <ConceptDetails concept={mockConcept} memesApi={api} />
      </MemoryRouter>
    )
    expect(screen.getByText('Loading...')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByText('Loading...')).not.toBeInTheDocument())
  })

  it('renders meme cards returned by the API', async () => {
    const meme = { id: 'meme-1', imageUrl: '/img.jpg', text: [], tags: [], excluded: false }
    const api = makeMockApi({
      getTopImagesForConcept: vi.fn().mockResolvedValue({ items: [meme], hasNext: false }),
      getImageUrl: vi.fn().mockReturnValue('http://example.com/img.jpg'),
      getImageIsExcluded: vi.fn().mockResolvedValue(false),
    })
    render(
      <MemoryRouter>
        <ConceptDetails concept={mockConcept} memesApi={api} />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByRole('img', { name: 'meme-1' })).toBeInTheDocument())
  })

  it('shows "Nothing to show" when the API returns no memes', async () => {
    const api = makeMockApi()
    render(
      <MemoryRouter>
        <ConceptDetails concept={mockConcept} memesApi={api} />
      </MemoryRouter>
    )
    await waitFor(() => expect(screen.getByText('Nothing to show')).toBeInTheDocument())
  })

  it('in StrictMode loading resolves correctly (no stuck spinner)', async () => {
    const api = makeMockApi()
    render(
      <StrictMode>
        <MemoryRouter>
          <ConceptDetails concept={mockConcept} memesApi={api} />
        </MemoryRouter>
      </StrictMode>
    )
    await waitFor(() => expect(screen.queryByText('Loading...')).not.toBeInTheDocument())
  })

  it('re-fetches when concept ID changes', async () => {
    const api = makeMockApi()
    const conceptA: Concept = { id: 1, name: 'cats' }
    const conceptB: Concept = { id: 2, name: 'dogs' }
    const { rerender } = render(
      <MemoryRouter><ConceptDetails concept={conceptA} memesApi={api} /></MemoryRouter>
    )
    await act(async () => {})

    rerender(<MemoryRouter><ConceptDetails concept={conceptB} memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    expect(api.getTopImagesForConcept).toHaveBeenCalledWith(2)
    expect(api.getTopImagesForConcept).toHaveBeenCalledTimes(2)
  })
})
