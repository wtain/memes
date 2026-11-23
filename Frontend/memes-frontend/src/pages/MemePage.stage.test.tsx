import { render, screen, waitFor, act, fireEvent } from '@testing-library/react'
import { StrictMode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import MemePage from './MemePage'
import { FeaturesProvider } from '../features/FeaturesProvider'
import type { Stage } from '../features/visibility'
import { ComicsRequestError } from '../api/errors'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'

function renderMeme(stage: Stage, overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({
    getDescriptions: vi.fn().mockResolvedValue([{ promptKey: 'plain', text: 'a cat', feedback: null }]),
    ...overrides,
  })
  const router = createMemoryRouter(
    [{ path: '/memes/:id', element: <MemePage memesApi={api} /> }],
    { initialEntries: [`/memes/${DEFAULT_MOCK_MEME.id}`] },
  )
  render(
    <StrictMode>
      <FeaturesProvider stage={stage} loadCapabilities={() => Promise.resolve(null)}>
        <RouterProvider router={router} />
      </FeaturesProvider>
    </StrictMode>,
  )
  return api
}

describe('MemePage on a prod build', () => {
  it('shows the meme and the read-only blocks but none of the curator controls', async () => {
    const api = renderMeme('prod')
    await screen.findByText(`Meme ${DEFAULT_MOCK_MEME.id}`)
    await screen.findByText('a cat')
    await screen.findByTestId('popularity-value')

    expect(screen.queryByRole('heading', { name: 'Profile' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Situation')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Description note')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Save note' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Flagged')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Approve/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Reject/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Set popularity')).not.toBeInTheDocument()

    // nothing the curator controls read or any comics endpoint is asked for
    expect(api.getCuratedProfile).not.toHaveBeenCalled()
    expect(api.getImageIsFlagged).not.toHaveBeenCalled()
    expect(api.comicsByImage).not.toHaveBeenCalled()
    expect(api.comicsAddability).not.toHaveBeenCalled()
    expect(api.listComics).not.toHaveBeenCalled()
    expect(api.getMeme).toHaveBeenCalledTimes(1)
  })
})

describe('MemePage on a dev build', () => {
  it('keeps every curator control and the comics block', async () => {
    const api = renderMeme('dev')
    await screen.findByLabelText('Situation')
    await screen.findByLabelText('Description note')
    expect(screen.getByLabelText('Flagged')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: /^Approve/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Reject/ })).toBeInTheDocument()
    expect(await screen.findByLabelText('Set popularity')).toBeInTheDocument()
    // the comics block sits in the Stats tab; role queries skip a hidden panel
    fireEvent.click(screen.getByRole('tab', { name: 'Stats' }))
    expect(await screen.findByRole('button', { name: 'Add to comics…' })).toBeInTheDocument()
    expect(api.comicsByImage).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
  })

  it('renders the meme without a banner when every comics call answers 404', async () => {
    const notFound = () => Promise.reject(new ComicsRequestError(404, {}))
    const api = renderMeme('dev', {
      comicsByImage: vi.fn().mockImplementation(notFound),
      comicsAddability: vi.fn().mockImplementation(notFound),
      listComics: vi.fn().mockImplementation(notFound),
    })
    await screen.findByText(`Meme ${DEFAULT_MOCK_MEME.id}`)
    await screen.findByLabelText('Situation')
    await waitFor(() => expect(api.comicsByImage).toHaveBeenCalled())
    await act(async () => {})
    fireEvent.click(screen.getByRole('tab', { name: 'Stats' }))  // role queries skip a hidden panel
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add to comics…' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Create comics' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('comics-membership')).not.toBeInTheDocument()
  })
})
