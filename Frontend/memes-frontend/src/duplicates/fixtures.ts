import type { ClusterSimilarityPair, ClusterSimilarityResponse } from '../types/generated/all'

export function makeSimilarityPair(overrides: Partial<ClusterSimilarityPair> = {}): ClusterSimilarityPair {
  return { image_id1: 'a', image_id2: 'b', overlap: 0.5, candidates: [], ...overrides }
}

export function makeSimilarityResponse(overrides: Partial<ClusterSimilarityResponse> = {}): ClusterSimilarityResponse {
  return { pairs: [], capped: false, ...overrides }
}
