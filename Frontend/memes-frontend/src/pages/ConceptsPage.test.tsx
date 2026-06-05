import { render, screen, act, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import ConceptsPage from './ConceptsPage'
import { makeMockApi } from '../test/mockApi'

describe('ConceptsPage', () => {
  it('calls listConcepts once per mount (production behaviour)', async () => {
    const api = makeMockApi()
    render(<MemoryRouter><ConceptsPage memesApi={api} /></MemoryRouter>)
    await act(async () => {})
    expect(api.listConcepts).toHaveBeenCalledTimes(1)
  })

  it('renders a row for each concept returned', async () => {
    const api = makeMockApi({
      listConcepts: vi.fn().mockResolvedValue([
        { id: 1, name: 'cats' },
        { id: 2, name: 'dogs' },
      ]),
    })
    render(<MemoryRouter><ConceptsPage memesApi={api} /></MemoryRouter>)
    await waitFor(() => {
      expect(screen.getByText('cats')).toBeInTheDocument()
      expect(screen.getByText('dogs')).toBeInTheDocument()
    })
  })

  it('in StrictMode renders concepts without duplication', async () => {
    const api = makeMockApi({
      listConcepts: vi.fn().mockResolvedValue([{ id: 1, name: 'cats' }]),
    })
    render(
      <StrictMode>
        <MemoryRouter><ConceptsPage memesApi={api} /></MemoryRouter>
      </StrictMode>
    )
    await waitFor(() => expect(screen.getByText('cats')).toBeInTheDocument())
    expect(screen.getAllByText('cats')).toHaveLength(1)
  })
})
