import { bestClipDistance, overlapPercent, sortPairs } from './pairs'
import { makeSimilarityPair } from './fixtures'

describe('sortPairs', () => {
  it('puts the closest clip distance first', () => {
    const far = makeSimilarityPair({ image_id1: 'a', image_id2: 'b', candidates: [{ distance: 0.09, source: 'clip' }] })
    const near = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', candidates: [{ distance: 0.02, source: 'clip' }] })
    expect(sortPairs([far, near])).toEqual([near, far])
  })

  it('puts pairs without a clip candidate after those with one, ocr_text before none', () => {
    const none = makeSimilarityPair({ image_id1: 'a', image_id2: 'b' })
    const text = makeSimilarityPair({ image_id1: 'a', image_id2: 'c', candidates: [{ distance: 0.01, source: 'ocr_text' }] })
    const clip = makeSimilarityPair({ image_id1: 'b', image_id2: 'c', candidates: [{ distance: 0.9, source: 'clip' }] })
    expect(sortPairs([none, text, clip])).toEqual([clip, text, none])
  })

  it('uses the clip distance when a pair also has an ocr_text candidate', () => {
    const both = makeSimilarityPair({ candidates: [{ distance: 0.08, source: 'ocr_text' }, { distance: 0.04, source: 'clip' }] })
    expect(bestClipDistance(both)).toBe(0.04)
  })

  it('breaks ties by ids and does not mutate the input', () => {
    const second = makeSimilarityPair({ image_id1: 'b', image_id2: 'c' })
    const first = makeSimilarityPair({ image_id1: 'a', image_id2: 'z' })
    const input = [second, first]
    expect(sortPairs(input)).toEqual([first, second])
    expect(input).toEqual([second, first])
  })
})

describe('overlapPercent', () => {
  it('formats a coefficient as a whole percent', () => {
    expect(overlapPercent(0.75)).toBe('75%')
    expect(overlapPercent(0)).toBe('0%')
  })

  it('returns null for a missing overlap, never NaN', () => {
    expect(overlapPercent(null)).toBeNull()
  })
})
