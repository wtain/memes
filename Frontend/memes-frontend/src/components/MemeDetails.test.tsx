import { render, screen, act, waitFor, fireEvent, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { MemeDetails } from './MemeDetails'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'
import { getCardImg, queryCardImg } from '../test/cardQueries'
import { useFeature } from '../features/useFeature'

vi.mock('../features/useFeature', async importOriginal => {
  const actual = await importOriginal<typeof import('../features/useFeature')>()
  return { ...actual, useFeature: vi.fn(actual.useFeature) }
})

function renderMemeDetails(meme = DEFAULT_MOCK_MEME, overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi(overrides)
  const result = render(
    <MemoryRouter>
      <MemeDetails meme={meme} memesApi={api} />
    </MemoryRouter>
  )
  return { api, ...result }
}

// Inactive tab panels stay mounted but hidden, and role queries skip hidden elements.
function openTab(name: string) {
  fireEvent.click(screen.getByRole('tab', { name }))
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

  describe('popularity', () => {
    it('shows the popularity block for the meme', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getMemePopularity: vi.fn().mockResolvedValue({ popularity: 7, status: 'normal', breakdown: [{ source: 'own', count: 7 }] }),
      })
      await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('7'))
    })

    it('never shows the previous meme number after navigating', async () => {
      const api = makeMockApi({
        getMemePopularity: vi.fn()
          .mockResolvedValueOnce({ popularity: 7, status: 'normal', breakdown: [] })
          .mockReturnValueOnce(new Promise(() => {})),
      })
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(<MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>)
      await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('7'))
      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(api.getMemePopularity).toHaveBeenCalledWith('meme-B')
      expect(screen.queryByTestId('popularity-value')).not.toBeInTheDocument()
    })
  })

  describe('saves', () => {
    it('shows the save count for the meme', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getMemeSaves: vi.fn().mockResolvedValue({ imageId: 'x', count: 3, lastSavedAt: null }),
      })
      await waitFor(() => expect(screen.getByTestId('save-count-value')).toHaveTextContent('3'))
    })

    it('never shows the previous meme count after navigating', async () => {
      const api = makeMockApi({
        getMemeSaves: vi.fn()
          .mockResolvedValueOnce({ imageId: 'meme-A', count: 3, lastSavedAt: null })
          .mockReturnValueOnce(new Promise(() => {})),
      })
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(<MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>)
      await waitFor(() => expect(screen.getByTestId('save-count-value')).toHaveTextContent('3'))
      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(api.getMemeSaves).toHaveBeenCalledWith('meme-B')
      expect(screen.queryByTestId('save-count-value')).not.toBeInTheDocument()
    })
  })

  describe('impressions', () => {
    it('shows the impression count for the meme', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getMemeImpressions: vi.fn().mockResolvedValue({
          imageId: 'x', count: 7, countWindow: 2, windowDays: 30, lastShownAt: null }),
      })
      await waitFor(() => expect(screen.getByTestId('impression-count')).toHaveTextContent('7'))
    })

    it('never shows the previous meme count after navigating', async () => {
      const api = makeMockApi({
        getMemeImpressions: vi.fn()
          .mockResolvedValueOnce({ imageId: 'meme-A', count: 7, countWindow: 2, windowDays: 30, lastShownAt: null })
          .mockReturnValueOnce(new Promise(() => {})),
      })
      const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
      const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
      const { rerender } = render(<MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>)
      await waitFor(() => expect(screen.getByTestId('impression-count')).toHaveTextContent('7'))
      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      expect(api.getMemeImpressions).toHaveBeenCalledWith('meme-B')
      expect(screen.queryByTestId('impression-count')).not.toBeInTheDocument()
    })
  })

  describe('popularity after the flag toggle', () => {
    it('refetches the block once the flag call succeeds and shows the new response', async () => {
      const api = makeMockApi({
        getImageIsFlagged: vi.fn().mockResolvedValue(true),
        unmarkImageIsFlagged: vi.fn().mockResolvedValue(undefined),
        getMemePopularity: vi.fn()
          .mockResolvedValueOnce({ popularity: 0, status: 'discarded', breakdown: [] })
          .mockResolvedValueOnce({ popularity: 3, status: 'normal', breakdown: [] }),
      })
      render(<MemoryRouter><MemeDetails meme={DEFAULT_MOCK_MEME} memesApi={api} /></MemoryRouter>)
      await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('0'))
      openTab('Curate')
      const checkbox = await screen.findByRole('checkbox')
      await waitFor(() => expect(checkbox).toBeChecked())
      fireEvent.click(checkbox)
      await waitFor(() => expect(screen.getByTestId('popularity-value')).toHaveTextContent('3'))
      expect(api.getMemePopularity).toHaveBeenCalledTimes(2)
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
      openTab('Curate')

      expect(api.getMeme).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('a cat wearing a hat')
    })

    it('renders an empty textarea when no note is set', async () => {
      renderMemeDetails(DEFAULT_MOCK_MEME)
      await act(async () => {})
      openTab('Curate')

      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
    })

    it('clicking Save calls setDescriptionNote with the current textarea value', async () => {
      const { api } = renderMemeDetails(DEFAULT_MOCK_MEME)
      await act(async () => {})
      openTab('Curate')

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
      openTab('Curate')

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
      openTab('Curate')
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('note for A')

      rerender(<MemoryRouter><MemeDetails meme={memeBFromList} memesApi={api} /></MemoryRouter>)
      await act(async () => {})
      openTab('Curate')  // the tab resets to Info with the meme
      expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
    })

    it('disables the textarea and Save/Clear buttons until the note has loaded', async () => {
      let resolveGetMeme: (meme: typeof DEFAULT_MOCK_MEME) => void = () => {}
      const getMemePromise = new Promise<typeof DEFAULT_MOCK_MEME>(resolve => { resolveGetMeme = resolve })
      renderMemeDetails(DEFAULT_MOCK_MEME, {
        getMeme: vi.fn().mockReturnValue(getMemePromise),
      })
      openTab('Curate')

      expect(screen.getByRole('textbox', { name: 'Description note' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Save note' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Clear note' })).toBeDisabled()

      await act(async () => {
        resolveGetMeme({ ...DEFAULT_MOCK_MEME, descriptionNote: 'a cat wearing a hat' })
      })

      expect(screen.getByRole('textbox', { name: 'Description note' })).not.toBeDisabled()
      expect(screen.getByRole('button', { name: 'Save note' })).not.toBeDisabled()
      expect(screen.getByRole('button', { name: 'Clear note' })).not.toBeDisabled()
    })
  })

  describe('similarity mode toggle', () => {
    it('defaults to Visual and fetches with source=image', async () => {
      const { api } = renderMemeDetails()
      await act(async () => {})
      expect(api.similarMemes).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id, "image")
      openTab('Similar')
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
      openTab('Similar')

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
      expect(getCardImg('vis-1')).toBeInTheDocument()
      openTab('Similar')

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      expect(queryCardImg('vis-1')).not.toBeInTheDocument()
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
      openTab('Similar')

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
      openTab('Similar')

      screen.getByRole('button', { name: 'Semantic' }).click()
      await act(async () => {})

      rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
      await act(async () => {})

      expect(api.similarMemes).toHaveBeenCalledWith('meme-B', "description")
    })
  })
})

describe('MemeDetails share actions row', () => {
  function row() {
    return screen.getByRole('link', { name: 'Open image' }).parentElement!
  }

  it('shows the labelled row with Open image', async () => {
    renderMemeDetails()
    await act(async () => {})
    const group = row()
    expect(group).toHaveAttribute('aria-label', 'Share actions')
    for (const name of ['Copy image', 'Download', 'Copy link']) {
      expect(within(group).getByRole('button', { name })).toBeInTheDocument()
    }
    expect(screen.getByRole('link', { name: 'Open image' })).toHaveAttribute('target', '_blank')
  })

  it('sits after the zoomable image and before the tags block', async () => {
    renderMemeDetails()
    await act(async () => {})
    const group = row()
    const zoomIn = screen.getByRole('button', { name: '+' })
    const tags = screen.getByText('Tags:')
    expect(zoomIn.compareDocumentPosition(group) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(group.compareDocumentPosition(tags) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('has dropped the legacy cross-origin download anchor', async () => {
    renderMemeDetails()
    await act(async () => {})
    expect(screen.queryByText(/⬇/)).not.toBeInTheDocument()
    expect(document.querySelector('a[download]')).toBeNull()
    expect(within(row()).getAllByRole('button', { name: /download/i })).toHaveLength(1)
  })
})

describe('MemeDetails panel tabs', () => {
  afterEach(() => { vi.mocked(useFeature).mockReset() })

  function open(name: string) {
    fireEvent.click(screen.getByRole('tab', { name }))
  }

  it('shows Info content by default', async () => {
    renderMemeDetails(DEFAULT_MOCK_MEME, {
      getDescriptions: vi.fn().mockResolvedValue([
        { promptKey: 'general_description', text: 'A cat.', modelUsed: 'llava', createdAt: '2026-07-19T12:00:00' },
      ]),
    })
    await act(async () => {})
    expect(screen.getByRole('tab', { name: 'Info' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Text Lines:')).toBeVisible()
    expect(screen.getByText('Descriptions:')).toBeVisible()
    expect(screen.getByText('Tags:')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Visual', hidden: true })).not.toBeVisible()
    expect(screen.getByTestId('popularity-value')).not.toBeVisible()
    expect(screen.getByLabelText('Description note')).not.toBeVisible()
  })

  it('Similar tab shows the toggle and the grid', async () => {
    renderMemeDetails(DEFAULT_MOCK_MEME, {
      similarMemes: vi.fn().mockResolvedValue({ items: [{ id: 'vis-1', imageUrl: '/vis-1.jpg' }], facets: [], hasNext: false }),
    })
    await act(async () => {})
    open('Similar')
    expect(screen.getByRole('button', { name: 'Visual' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Semantic' })).toBeVisible()
    expect(getCardImg('vis-1')).toBeVisible()
  })

  it('Stats tab shows popularity, saves and impressions', async () => {
    renderMemeDetails(DEFAULT_MOCK_MEME, {
      getMemePopularity: vi.fn().mockResolvedValue({ popularity: 7, status: 'normal', breakdown: [] }),
      getMemeSaves: vi.fn().mockResolvedValue({ imageId: 'x', count: 3, lastSavedAt: null }),
      getMemeImpressions: vi.fn().mockResolvedValue({ imageId: 'x', count: 9, countWindow: 2, windowDays: 30, lastShownAt: null }),
    })
    await act(async () => {})
    open('Stats')
    expect(screen.getByTestId('popularity-value')).toBeVisible()
    expect(screen.getByTestId('save-count-value')).toBeVisible()
    expect(screen.getByTestId('impression-count')).toBeVisible()
  })

  it('Curate tab exists with the curator feature and holds the note and the flag toggle', async () => {
    renderMemeDetails()
    await act(async () => {})
    open('Curate')
    expect(screen.getByRole('textbox', { name: 'Description note' })).toBeVisible()
    expect(screen.getByRole('checkbox')).toBeVisible()
  })

  it('has no Curate tab and no curator controls without the curator feature', async () => {
    vi.mocked(useFeature).mockImplementation(f => f !== 'curator')
    renderMemeDetails()
    await act(async () => {})
    expect(screen.queryByRole('tab', { name: 'Curate' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Description note')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })

  it('keeps hidden panels mounted: similar and concepts fetch once without opening any tab', async () => {
    const { api } = renderMemeDetails()
    await act(async () => {})
    expect(api.similarMemes).toHaveBeenCalledTimes(1)
    expect(api.getTopConceptsForImage).toHaveBeenCalledTimes(1)
    // the Stats blocks sit in a hidden panel and still fetch on mount, once
    expect(api.getMemePopularity).toHaveBeenCalledTimes(1)
    expect(api.getMemeSaves).toHaveBeenCalledTimes(1)
    expect(api.getMemeImpressions).toHaveBeenCalledTimes(1)
  })

  it('clears the previous meme per-meme state at once when the meme changes, keeping the similar source', async () => {
    const pending = () => new Promise<never>(() => {})
    const api = makeMockApi({
      getDescriptions: vi.fn()
        .mockResolvedValueOnce([{ promptKey: 'general_description', text: 'text of A', modelUsed: 'm', createdAt: '2026-07-19T12:00:00' }])
        .mockReturnValueOnce(pending()),
      getMeme: vi.fn()
        .mockResolvedValueOnce({ ...DEFAULT_MOCK_MEME, id: 'meme-A', descriptionNote: 'note of A' })
        .mockReturnValueOnce(pending()),
      getImageIsFlagged: vi.fn().mockResolvedValueOnce(true).mockReturnValueOnce(pending()),
      getTopConceptsForImage: vi.fn().mockResolvedValueOnce([{ id: 1, name: 'cats' }]).mockReturnValueOnce(pending()),
      similarMemes: vi.fn()
        .mockResolvedValueOnce({ items: [{ id: 'sim-A', imageUrl: '/a.jpg' }], facets: [], hasNext: false })
        .mockReturnValueOnce(pending()),
    })
    const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
    const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
    const { rerender } = render(<MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    openTab('Curate')
    expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('note of A')
    expect(screen.getByText(/text of A/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeChecked()
    expect(screen.getByText('cats')).toBeInTheDocument()
    expect(getCardImg('sim-A')).toBeInTheDocument()

    rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
    // B's own fetches are still pending
    openTab('Curate')
    expect(screen.getByRole('textbox', { name: 'Description note' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Description note' })).toHaveValue('')
    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.getByRole('checkbox')).not.toBeChecked()
    expect(screen.queryByText(/text of A/)).not.toBeInTheDocument()
    expect(screen.queryByText('cats')).not.toBeInTheDocument()
    expect(queryCardImg('sim-A')).not.toBeInTheDocument()
  })

  it('opens the next meme at 100% zoom', async () => {
    const api = makeMockApi()
    const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
    const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
    const { rerender } = render(<MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    fireEvent.click(screen.getByRole('button', { name: '+' }))
    expect(screen.getByText('125%')).toBeInTheDocument()
    rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('resets to Info when the meme changes', async () => {
    const api = makeMockApi()
    const memeA = { ...DEFAULT_MOCK_MEME, id: 'meme-A' }
    const memeB = { ...DEFAULT_MOCK_MEME, id: 'meme-B' }
    const { rerender } = render(<MemoryRouter><MemeDetails meme={memeA} memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    open('Stats')
    expect(screen.getByRole('tab', { name: 'Stats' })).toHaveAttribute('aria-selected', 'true')
    rerender(<MemoryRouter><MemeDetails meme={memeB} memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    expect(screen.getByRole('tab', { name: 'Info' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Stats' })).toHaveAttribute('aria-selected', 'false')
  })

  it('omits the concepts table header when there are no concepts', async () => {
    renderMemeDetails(DEFAULT_MOCK_MEME, { getTopConceptsForImage: vi.fn().mockResolvedValue([]) })
    await act(async () => {})
    open('Stats')
    expect(screen.queryByRole('columnheader', { name: 'Name' })).not.toBeInTheDocument()
  })

  it('shows the concepts table when there are concepts', async () => {
    renderMemeDetails(DEFAULT_MOCK_MEME, {
      getTopConceptsForImage: vi.fn().mockResolvedValue([{ id: 1, name: 'cats' }]),
    })
    await act(async () => {})
    open('Stats')
    expect(screen.getByRole('columnheader', { name: 'Name' })).toBeVisible()
  })
})
