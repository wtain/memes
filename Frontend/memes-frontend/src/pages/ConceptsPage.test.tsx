import { render, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import ConceptsPage from './ConceptsPage'
import type { MemesApi } from '../api/MemesApi'

function makeMockApi(overrides: Partial<MemesApi> = {}): MemesApi {
  return {
    searchMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateUntaggedMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateDuplicates: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getImageUrl: vi.fn().mockReturnValue('http://example.com/test.jpg'),
    listConcepts: vi.fn().mockResolvedValue([]),
    getTopImagesForConcept: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getTopConceptsForImage: vi.fn().mockResolvedValue([]),
    getMeme: vi.fn().mockResolvedValue({}),
    getConcept: vi.fn().mockResolvedValue({ id: 1, name: 'test' }),
    markImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    unmarkImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    getImageIsExcluded: vi.fn().mockResolvedValue(false),
    ...overrides,
  } as MemesApi
}

describe('ConceptsPage', () => {
  it('calls listConcepts exactly once on mount', async () => {
    const api = makeMockApi()
    render(
      <StrictMode>
        <MemoryRouter>
          <ConceptsPage memesApi={api} />
        </MemoryRouter>
      </StrictMode>
    )
    await act(async () => {})
    expect(api.listConcepts).toHaveBeenCalledTimes(1)
  })
})
