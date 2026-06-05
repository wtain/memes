import type { MemesApi } from '../api/MemesApi'
import type { Meme } from '../types/generated/all'

export const DEFAULT_MOCK_MEME: Meme = {
  id: 'meme-1',
  imageUrl: '/images/test.jpg',
  text: [],
  tags: [],
  excluded: false,
}

export function makeMockApi(overrides: Partial<MemesApi> = {}): MemesApi {
  return {
    searchMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateUntaggedMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateDuplicates: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getImageUrl: vi.fn().mockReturnValue('http://example.com/test.jpg'),
    listConcepts: vi.fn().mockResolvedValue([]),
    getTopImagesForConcept: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getTopConceptsForImage: vi.fn().mockResolvedValue([]),
    getMeme: vi.fn().mockResolvedValue(DEFAULT_MOCK_MEME),
    getConcept: vi.fn().mockResolvedValue({ id: 1, name: 'test-concept' }),
    markImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    unmarkImageIsExcluded: vi.fn().mockResolvedValue(undefined),
    getImageIsExcluded: vi.fn().mockResolvedValue(false),
    ...overrides,
  } as MemesApi
}
