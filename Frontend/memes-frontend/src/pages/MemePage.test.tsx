import { render, screen, act, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import MemePage from './MemePage'
import { makeMockApi, DEFAULT_MOCK_MEME } from '../test/mockApi'

function renderMemePage(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi(overrides)
  const result = render(
    <MemoryRouter initialEntries={[`/memes/${DEFAULT_MOCK_MEME.id}`]}>
      <Routes>
        <Route path="/memes/:id" element={<MemePage memesApi={api} />} />
      </Routes>
    </MemoryRouter>
  )
  return { api, ...result }
}

describe('MemePage', () => {
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
        <MemoryRouter initialEntries={[`/memes/${DEFAULT_MOCK_MEME.id}`]}>
          <Routes>
            <Route path="/memes/:id" element={<MemePage memesApi={api} />} />
          </Routes>
        </MemoryRouter>
      </StrictMode>
    )
    await waitFor(() =>
      expect(screen.getByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).toBeInTheDocument()
    )
    expect(screen.getAllByText(`Meme ${DEFAULT_MOCK_MEME.id}`)).toHaveLength(1)
  })
})
