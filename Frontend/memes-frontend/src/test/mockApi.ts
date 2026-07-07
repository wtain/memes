import type { MemesApi } from '../api/MemesApi'
import type { Meme } from '../types/generated/all'

export const DEFAULT_MOCK_MEME: Meme = {
  id: 'meme-1',
  imageUrl: '/images/test.jpg',
  text: [],
  tags: [],
  flagged: false,
}

export function makeMockApi(overrides: Partial<MemesApi> = {}): MemesApi {
  return {
    searchMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateUntaggedMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateNoOcrMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateDuplicates: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getImageUrl: vi.fn().mockReturnValue('http://example.com/test.jpg'),
    listConcepts: vi.fn().mockResolvedValue([]),
    getTopImagesForConcept: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getTopConceptsForImage: vi.fn().mockResolvedValue([]),
    getMeme: vi.fn().mockResolvedValue(DEFAULT_MOCK_MEME),
    getConcept: vi.fn().mockResolvedValue({ id: 1, name: 'test-concept' }),
    iterateFlaggedMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    markImageIsFlagged: vi.fn().mockResolvedValue(undefined),
    unmarkImageIsFlagged: vi.fn().mockResolvedValue(undefined),
    getImageIsFlagged: vi.fn().mockResolvedValue(false),
    ...overrides,
  } as MemesApi
}
