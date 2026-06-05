import { render, act } from '@testing-library/react'
import { StrictMode } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import MemePage from './MemePage'
import type { MemesApi } from '../api/MemesApi'
import type { Meme } from '../types/generated/all'

const mockMeme: Meme = {
  id: 'meme-abc',
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

describe('MemePage', () => {
  it('calls getMeme exactly once on mount', async () => {
    const api = makeMockApi()
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/memes/meme-abc']}>
          <Routes>
            <Route path="/memes/:id" element={<MemePage memesApi={api} />} />
          </Routes>
        </MemoryRouter>
      </StrictMode>
    )
    await act(async () => {})
    expect(api.getMeme).toHaveBeenCalledTimes(1)
    expect(api.getMeme).toHaveBeenCalledWith('meme-abc')
  })
})
