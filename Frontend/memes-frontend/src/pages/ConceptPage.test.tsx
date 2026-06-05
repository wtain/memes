import { render, screen, act, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import ConceptPage from './ConceptPage'
import { makeMockApi } from '../test/mockApi'
import type { Concept } from '../types/generated/all'

const mockConcept: Concept = { id: 42, name: 'test-concept' }

function renderConceptPage(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi({ getConcept: vi.fn().mockResolvedValue(mockConcept), ...overrides })
  const result = render(
    <MemoryRouter initialEntries={['/concepts/42']}>
      <Routes>
        <Route path="/concepts/:id" element={<ConceptPage memesApi={api} />} />
      </Routes>
    </MemoryRouter>
  )
  return { api, ...result }
}

describe('ConceptPage', () => {
  it('calls getConcept once per mount (production behaviour)', async () => {
    const { api } = renderConceptPage()
    await act(async () => {})
    expect(api.getConcept).toHaveBeenCalledTimes(1)
    expect(api.getConcept).toHaveBeenCalledWith(42)
  })

  it('renders the concept name after fetching', async () => {
    renderConceptPage()
    await waitFor(() => expect(screen.getByText('Concept: test-concept')).toBeInTheDocument())
  })

  it('shows loading placeholder before fetch resolves', () => {
    renderConceptPage()
    expect(screen.getByText('Loading...')).toBeInTheDocument()
  })

  it('in StrictMode concept name renders exactly once', async () => {
    const api = makeMockApi({ getConcept: vi.fn().mockResolvedValue(mockConcept) })
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/concepts/42']}>
          <Routes>
            <Route path="/concepts/:id" element={<ConceptPage memesApi={api} />} />
          </Routes>
        </MemoryRouter>
      </StrictMode>
    )
    await waitFor(() => expect(screen.getByText('Concept: test-concept')).toBeInTheDocument())
    expect(screen.getAllByText('Concept: test-concept')).toHaveLength(1)
  })
})
