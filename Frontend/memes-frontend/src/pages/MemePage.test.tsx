import { render, screen, act, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StrictMode } from 'react'
import { createMemoryRouter, Link, MemoryRouter, Route, Routes, RouterProvider } from 'react-router-dom'
import MemePage from './MemePage'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'
import { useFeature } from '../features/useFeature'

vi.mock('../features/useFeature', async importOriginal => {
  const actual = await importOriginal<typeof import('../features/useFeature')>()
  return { ...actual, useFeature: vi.fn(actual.useFeature) }
})

// A data router: the curator panel's unsaved-changes guard needs one (as the app's createBrowserRouter is).
function memeRouter(api: ReturnType<typeof makeMockApi>) {
  return createMemoryRouter(
    [{ path: '/memes/:id', element: <MemePage memesApi={api} /> }],
    { initialEntries: [`/memes/${DEFAULT_MOCK_MEME.id}`] },
  )
}

function renderMemePage(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi(overrides)
  const result = render(<RouterProvider router={memeRouter(api)} />)
  return { api, ...result }
}

describe('MemePage', () => {
  afterEach(() => { vi.mocked(useFeature).mockRestore() })

  it('calls getMeme once per mount, plus once more from MemeDetails\' own note fetch', async () => {
    // MemePage fetches the meme itself; MemeDetails independently fetches getMeme again
    // to obtain the authoritative descriptionNote (it never trusts the meme prop for that
    // field -- see MemeDetails.tsx). Two calls to the same id is the expected, accepted
    // cost of that design, not a regression.
    const { api } = renderMemePage()
    await act(async () => {})
    expect(api.getMeme).toHaveBeenCalledTimes(2)
    expect(api.getMeme).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
  })

  it('renders the curator panel below the details for the loaded meme', async () => {
    const { api } = renderMemePage()
    await screen.findByLabelText('Situation')
    expect(screen.getByRole('heading', { name: 'Profile' })).toBeInTheDocument()
    expect(api.getCuratedProfile).toHaveBeenCalledWith(DEFAULT_MOCK_MEME.id)
  })

  it('shows loading placeholder before fetch resolves', () => {
    renderMemePage()
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('renders the meme heading after fetching', async () => {
    renderMemePage()
    await waitFor(() =>
      expect(screen.getByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).toBeInTheDocument()
    )
  })

  it('in StrictMode meme heading renders exactly once', async () => {
    const api = makeMockApi()
    render(
      <StrictMode>
        <RouterProvider router={memeRouter(api)} />
      </StrictMode>
    )
    await waitFor(() =>
      expect(screen.getByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).toBeInTheDocument()
    )
    expect(screen.getAllByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).toHaveLength(1)
  })
  it('shows a skeleton status while the meme loads', () => {
    renderMemePage({ getMeme: vi.fn((): Promise<typeof DEFAULT_MOCK_MEME> => new Promise(() => {})) })
    expect(screen.getByRole('status')).toBeInTheDocument()
    expect(screen.queryByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).not.toBeInTheDocument()
  })

  it('shows the error state with Try again and Back to Explore when the fetch fails', async () => {
    const getMeme = vi.fn<(id: string) => Promise<typeof DEFAULT_MOCK_MEME>>().mockRejectedValueOnce(new Error('boom'))
    const { api } = renderMemePage({ getMeme })
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this meme')
    expect(screen.getByText(/It may have been removed/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to Explore' })).toHaveAttribute('href', '/explore')
    getMeme.mockResolvedValue(DEFAULT_MOCK_MEME)
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).toBeInTheDocument()
    expect(api.getMeme).toHaveBeenCalled()
  })

  it('a late response for the previous id is ignored', async () => {
    let resolveA: (m: typeof DEFAULT_MOCK_MEME) => void = () => {}
    const getMeme = vi.fn((id: string) =>
      id === 'a'
        ? new Promise<typeof DEFAULT_MOCK_MEME>(r => { resolveA = r })
        : Promise.resolve({ ...DEFAULT_MOCK_MEME, id }))
    const api = makeMockApi({ getMeme })
    // No curator panel here: its unsaved-changes guard needs a data router, and navigating a data
    // router trips a jsdom/undici AbortSignal mismatch, so this test uses a plain MemoryRouter.
    vi.mocked(useFeature).mockReturnValue(false)
    // A plain MemoryRouter: navigating a data router trips a jsdom/undici AbortSignal mismatch.
    render(
      <MemoryRouter initialEntries={['/memes/a']}>
        <Link to="/memes/b">go b</Link>
        <Routes><Route path="/memes/:id" element={<MemePage memesApi={api} />} /></Routes>
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('link', { name: 'go b' }))
    expect(await screen.findByText('Meme b')).toBeInTheDocument()
    await act(async () => { resolveA({ ...DEFAULT_MOCK_MEME, id: 'a' }) })
    expect(screen.getByText('Meme b')).toBeInTheDocument()
    expect(screen.queryByText('Meme a')).not.toBeInTheDocument()
  })
})
