import { render, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { MemeDetails } from './MemeDetails'
import type { MemesApi } from '../api/MemesApi'
import type { Meme } from '../types/generated/all'

const mockMeme: Meme = {
  id: 'meme-1',
  imageUrl: '/images/test.jpg',
  text: [],
  tags: [],
  excluded: false,
}

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
    getMeme: vi.fn().mockResolvedValue(mockMeme),
    getConcept: vi.fn().mockResolvedValue({ id: 1, name: 'test' }),
    markImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    unmarkImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    getImageIsExcluded: vi.fn().mockResolvedValue(false),
    ...overrides,
  } as MemesApi
}

function renderInStrictMode(ui: React.ReactElement) {
  return render(<StrictMode>{ui}</StrictMode>)
}

describe('MemeDetails', () => {
  it('calls getTopConceptsForImage exactly once on mount', async () => {
    const api = makeMockApi()
    renderInStrictMode(
      <MemoryRouter>
        <MemeDetails meme={mockMeme} memesApi={api} />
      </MemoryRouter>
    )
    await act(async () => {})
    expect(api.getTopConceptsForImage).toHaveBeenCalledTimes(1)
    expect(api.getTopConceptsForImage).toHaveBeenCalledWith('meme-1')
  })

  it('calls similarMemes exactly once on mount', async () => {
    const api = makeMockApi()
    renderInStrictMode(
      <MemoryRouter>
        <MemeDetails meme={mockMeme} memesApi={api} />
      </MemoryRouter>
    )
    await act(async () => {})
    expect(api.similarMemes).toHaveBeenCalledTimes(1)
    expect(api.similarMemes).toHaveBeenCalledWith('meme-1')
  })
})
