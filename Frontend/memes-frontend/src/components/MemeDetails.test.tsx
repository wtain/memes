import { render, screen, act, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { MemeDetails } from './MemeDetails'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'

function renderMemeDetails(meme = DEFAULT_MOCK_MEME, overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi(overrides)
  const result = render(
    <MemoryRouter>
      <MemeDetails meme={meme} memesApi={api} />
    </MemoryRouter>
  )
  return { api, ...result }
}

describe('MemeDetails', () => {
  describe('getTopConceptsForImage', () => {
    it('calls the backend once per mount (production behaviour)', async () => {
      const { api } = renderMemeDetails()
      await act(async () => {})
      expect(api.getTopConceptsForImage).toHaveBeenCalledTimes(1)
      expect(api.getTopConceptsForImage).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
    })

    it('renders concept rows returned by the API', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getTopConceptsForImage: vi.fn().mockResolvedValue([
          { id: 1, name: 'cats' },
          { id: 2, name: 'dogs' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByText('cats')).toBeInTheDocument()
        expect(screen.getByText('dogs')).toBeInTheDocument()
      })
    })

    it('re-fetches when meme ID changes', async () => {
      const api = makeMockApi({
        getTopConceptsForImage: vi.fn()
          .mockResolvedValueOnce([{ id: 1, name: 'cats' }])
          .mockResolvedValueOnce([{ id: 2, name: 'dogs' }]),
      })
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(
        <MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>
      )
      await waitFor(() => expect(screen.getByText('cats')).toBeInTheDocument())

      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await waitFor(() => expect(screen.getByText('dogs')).toBeInTheDocument())
      expect(api.getTopConceptsForImage).toHaveBeenCalledWith('meme-B')
    })

    it('in StrictMode the fetcher fires twice but concepts render once correctly', async () => {
      const api = makeMockApi({
        getTopConceptsForImage: vi.fn().mockResolvedValue([{ id: 1, name: 'cats' }]),
      })
      render(
        <StrictMode>
          <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
        </StrictMode>
      )
      await waitFor(() => expect(screen.getByText('cats')).toBeInTheDocument())
      // StrictMode double-fires the effect; cleanup flag discards the stale first result
      expect(api.getTopConceptsForImage).toHaveBeenCalledTimes(2)
      expect(screen.getAllByText('cats')).toHaveLength(1)
    })
  })

  describe('similarMemes', () => {
    it('calls the backend once per mount (production behaviour)', async () => {
      const { api } = renderMemeDetails()
      await act(async () => {})
      expect(api.similarMemes).toHaveBeenCalledTimes(1)
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
    })

    it('re-fetches when meme ID changes', async () => {
      const api = makeMockApi()
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(
        <MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(api.similarMemes).toHaveBeenCalledWith('meme-B')
      expect(api.similarMemes).toHaveBeenCalledTimes(2)
    })
  })
})
