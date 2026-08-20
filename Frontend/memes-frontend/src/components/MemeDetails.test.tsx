import { render, screen, act, waitFor, fireEvent } from '@testing-library/react'
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

    it('in StrictMode the fetcher fires once and concepts render correctly', async () => {
      const api = makeMockApi({
        getTopConceptsForImage: vi.fn().mockResolvedValue([{ id: 1, name: 'cats' }]),
      })
      render(
        <StrictMode>
          <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
        </StrictMode>
      )
      await waitFor(() => expect(screen.getByText('cats')).toBeInTheDocument())
      // Ref-guard prevents the duplicate StrictMode request; exactly one network call
      expect(api.getTopConceptsForImage).toHaveBeenCalledTimes(1)
      expect(screen.getAllByText('cats')).toHaveLength(1)
    })
  })

  describe('similarMemes', () => {
    it('calls the backend once per mount (production behaviour)', async () => {
      const { api } = renderMemeDetails()
      await act(async () => {})
      expect(api.similarMemes).toHaveBeenCalledTimes(1)
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "image")
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
      expect(api.similarMemes).toHaveBeenCalledWith('meme-B', "image")
      expect(api.similarMemes).toHaveBeenCalledTimes(2)
    })
  })

  describe('descriptions', () => {
    it('renders each description with its humanized prompt label', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat wearing a hat.', modelUsed: 'qwen2.5vl:7b', createdAt: '2026-07-18T12:00:00' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByText('General description:')).toBeInTheDocument()
        expect(screen.getByText(/A cat wearing a hat\./)).toBeInTheDocument()
      })
    })

    it('renders multiple descriptions', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-18T12:00:00' },
          { promptKey: 'humor_explanation', text: 'Because cats.', modelUsed: 'llava', createdAt: '2026-07-18T12:00:00' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByText('General description:')).toBeInTheDocument()
        expect(screen.getByText('Humor explanation:')).toBeInTheDocument()
      })
    })

    it('shows a quiet empty state when there are no descriptions', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([]),
      })
      await waitFor(() => {
        expect(screen.getByText('No description available')).toBeInTheDocument()
      })
    })
  })

  describe('description feedback', () => {
    it('renders Approve/Reject buttons per description', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
        ]),
      })
      await waitFor(() => {
        expect(screen.getByLabelText('Approve General description')).toBeInTheDocument()
        expect(screen.getByLabelText('Reject General description')).toBeInTheDocument()
      })
    })

    it('clicking Approve calls the API and updates the button state from the response', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: 'approved' }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      screen.getByLabelText('Approve General description').click()
      await act(async () => {})

      expect(api.setDescriptionFeedback).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, 'general_description', 'approve')
      expect(screen.getByLabelText('Approve General description')).toHaveClass('bg-green-600')
    })

    it('clicking the already-approved button again clears feedback', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00', feedback: 'approved' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: undefined }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(screen.getByLabelText('Approve General description')).toHaveClass('bg-green-600')

      screen.getByLabelText('Approve General description').click()
      await act(async () => {})

      expect(screen.getByLabelText('Approve General description')).not.toHaveClass('bg-green-600')
    })

    it('clicking Reject while approved switches directly to rejected', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00', feedback: 'approved' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: 'rejected' }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      screen.getByLabelText('Reject General description').click()
      await act(async () => {})

      expect(api.setDescriptionFeedback).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, 'general_description', 'reject')
      expect(screen.getByLabelText('Reject General description')).toHaveClass('bg-red-600')
      expect(screen.getByLabelText('Approve General description')).not.toHaveClass('bg-green-600')
    })

    it('feedback on one description does not affect another', async () => {
      const api = makeMockApi({
        getDescriptions: vi.fn().mockResolvedValue([
          { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
          { promptKey: 'humor_explanation', text: 'Because cats.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
        ]),
        setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: 'approved' }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      screen.getByLabelText('Approve General description').click()
      await act(async () => {})

      expect(screen.getByLabelText('Approve General description')).toHaveClass('bg-green-600')
      expect(screen.getByLabelText('Approve Humor explanation')).not.toHaveClass('bg-green-600')
    })
  })

  describe('description note', () => {
    it('renders the existing note text in a textarea', async () => {
      const { api } = renderMemeDetails(DEFAULT_MOCK_MEME, {
        getMeme: vi.fn().mockResolvedValue({ ...DEFAULT_MOCK_MEME, descriptionNote: 'a cat wearing a hat' }),
      })
      await act(async () => {})

      expect(api.getMeme).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('a cat wearing a hat')
    })

    it('renders an empty textarea when no note is set', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME)
      await act(async () => {})

      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
    })

    it('clicking Save calls setDescriptionNote with the current textarea value', async () => {
      const { api } = renderMemeDetails(DEFAULT_MOCK_MEME)
      await act(async () => {})

      const textarea = screen.getByRole('textbox', { name: 'Description note' })
      await act(async () => {
        fireEvent.change(textarea, { target: { value: 'a dog in sunglasses' } })
      })
      await act(async () => {
        screen.getByRole('button', { name: 'Save note' }).click()
      })

      expect(api.setDescriptionNote).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, 'a dog in sunglasses')
    })

    it('clicking Clear calls deleteDescriptionNote and empties the textarea', async () => {
      const { api } = renderMemeDetails(DEFAULT_MOCK_MEME, {
        getMeme: vi.fn().mockResolvedValue({ ...DEFAULT_MOCK_MEME, descriptionNote: 'a cat wearing a hat' }),
      })
      await act(async () => {})

      await act(async () => {
        screen.getByRole('button', { name: 'Clear note' }).click()
      })

      expect(api.deleteDescriptionNote).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
    })

    it('shows a blank textarea for an image with a note when opened from a list view, not the stale value from a prior meme', async () => {
      // Regression test for the exact bug this fix addresses: MemeDetails must never trust
      // meme.descriptionNote from the prop (which search()/list endpoints never populate) --
      // it must always fetch the authoritative value via getMeme.
      const api = makeMockApi({
        getMeme: vi.fn()
          .mockResolvedValueOnce({ ...DEFAULT_MOCK_MEME, id: 'meme-A', descriptionNote: 'note for A' })
          .mockResolvedValueOnce({ ...DEFAULT_MOCK_MEME, id: 'meme-B', descriptionNote: undefined }),
      })
      const memeAFromList = { ...DEFAULT_MOCK_MEME, id: 'meme-A' } // as returned by search(): no descriptionNote field
      const memeBFromList = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(
        <MemoryRouter><MemeDetails meme={memeAFromList} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('note for A')

      rerender(<MemoryRouter><MemeDetails meme={memeBFromList} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
    })
  })

  describe('similarity mode toggle', () => {
    it('defaults to Visual and fetches with source=image', async () => {
      const { api } = renderMemeDetails()
      await act(async () => {})
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "image")
      expect(screen.getByRole('button', { name: 'Visual' })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Semantic' })).toBeInTheDocument()
    })

    it('clicking Semantic refetches with source=description', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn()
          .mockResolvedValueOnce({ items: [], facets: [], hasNext: false })
          .mockResolvedValueOnce({ items: [{ id: 'sem-1', imageUrl: '/sem-1.jpg' }], facets: [], hasNext: false }),
      })
      render(
        <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "description")
      expect(api.similarMemes).toHaveBeenCalledTimes(2)
    })

    it('clears stale results and shows the Semantic empty state when the description fetch fails (e.g. no embedding yet)', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn()
          .mockResolvedValueOnce({ items: [{ id: 'vis-1', imageUrl: '/vis-1.jpg' }], facets: [], hasNext: false })
          .mockRejectedValueOnce(new Error('404')),
      })
      render(
        <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})
      expect(screen.getByAltText(/vis-1/)).toBeInTheDocument()

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      expect(screen.queryByAltText(/vis-1/)).not.toBeInTheDocument()
      expect(screen.getByText('No semantic similarity available for this image yet')).toBeInTheDocument()
    })

    it('shows the Visual empty-state message when there are no visual matches', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
      })
      await waitFor(() => {
        expect(screen.getByText('No similar images found')).toBeInTheDocument()
      })
    })

    it('shows the Semantic empty-state message when there are no semantic matches', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
      })
      render(
        <MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      expect(screen.getByText('No semantic similarity available for this image yet')).toBeInTheDocument()
    })

    it('carries the selected mode over when the meme changes', async () => {
      const api = makeMockApi({
        similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
      })
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(
        <MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>
      )
      await act(async () => {})

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      expect(api.similarMemes).toHaveBeenCalledWith('meme-B', "description")
    })
  })
})
