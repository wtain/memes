import { render, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { ConceptDetails } from './ConceptDetails'
import type { MemesApi } from '../api/MemesApi'
import type { Concept } from '../types/generated/all'

const mockConcept: Concept = { id: 1, name: 'test-concept' }

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
    getConcept: vi.fn().mockResolvedValue(mockConcept),
    markImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    unmarkImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    getImageIsExcluded: vi.fn().mockResolvedValue(false),
    ...overrides,
  } as MemesApi
}

describe('ConceptDetails', () => {
  it('calls getTopImagesForConcept exactly once on mount', async () => {
    const api = makeMockApi()
    render(
      <StrictMode>
        <MemoryRouter>
          <ConceptDetails concept={mockConcept} memesApi={api} />
        </MemoryRouter>
      </StrictMode>
    )
    await act(async () => {})
    expect(api.getTopImagesForConcept).toHaveBeenCalledTimes(1)
    expect(api.getTopImagesForConcept).toHaveBeenCalledWith(1)
  })
})
