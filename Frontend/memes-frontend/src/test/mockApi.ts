import type { MemesApi } from '../api/MemesApi'
import type { Meme } from '../types/generated/all'
import { makeResponse, TEST_TAXONOMY } from '../reply/fixtures'
import { makeCuratedProfile, makeCoverage, makeCoverageImages } from '../curator/fixtures'
import { makeDecision, makeProposalDetail, makeProposalPage } from '../recut/fixtures'

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
    getTrendingMemes: vi.fn().mockResolvedValue({ items: [], hasNext: false }),
    iterateNoOcrMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    iterateDuplicates: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    similarMemes: vi.fn().mockResolvedValue({ items: [], facets: [], hasNext: false }),
    getDescriptions: vi.fn().mockResolvedValue([]),
    getMemePopularity: vi.fn().mockResolvedValue({ popularity: 1, status: 'normal', breakdown: [{ source: 'own', count: 1 }] }),
    setMemePopularity: vi.fn().mockResolvedValue({ popularity: 1, status: 'normal', breakdown: [{ source: 'own', count: 1 }] }),
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
    setDescriptionFeedback: vi.fn().mockResolvedValue({ feedback: undefined }),
    setDescriptionNote: vi.fn().mockResolvedValue(undefined),
    deleteDescriptionNote: vi.fn().mockResolvedValue(undefined),
    getImageUrlById: vi.fn().mockImplementation((id: string) => `http://example.com/api/images/${id}`),
    getIngestionRunStatus: vi.fn().mockResolvedValue(null),
    getIngestionPending: vi.fn().mockResolvedValue([]),
    getIngestionClusters: vi.fn().mockResolvedValue({ items: [], next_cursor: null, has_next: false }),
    getIngestionTierBReview: vi.fn().mockResolvedValue({ items: [], next_cursor: null, has_next: false }),
    resolveIngestionCluster: vi.fn().mockResolvedValue({ rejected: [], kept: [], failed: [], move_failed: [] }),
    undoIngestionReject: vi.fn().mockResolvedValue({ image_id: '', status: 'pending' }),
    dismissDuplicateCluster: vi.fn().mockResolvedValue({ pairs: [] }),
    getClusterSimilarity: vi.fn().mockResolvedValue({ pairs: [] }),
    undoDismissDuplicates: vi.fn().mockResolvedValue(undefined),
    listDuplicateDecisions: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    listBatchNames: vi.fn().mockResolvedValue({
      names: ['trends_batch', 'move_flagged', 'unregister_deleted_images'],
    }),
    triggerBatchRun: vi.fn().mockResolvedValue({ run_id: 'run-1', status: 'running' }),
    listBatchRuns: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    getBatchMetrics: vi.fn().mockResolvedValue({ process: 'recut', items: [], unattributed: { counts: { proposed: 0, approved: 0, rejected: 0 }, metrics: { approve_rate: null, decided_share: null, pending: 0 } } }),
    searchMemeFromText: vi.fn().mockResolvedValue(makeResponse()),
    getTaxonomy: vi.fn().mockResolvedValue(TEST_TAXONOMY),
    putRating: vi.fn().mockResolvedValue({ queryHash: "a".repeat(64), imageId: "img", grade: 2 }),
    deleteRating: vi.fn().mockResolvedValue(undefined),
    getRatings: vi.fn().mockResolvedValue({ queryHash: "a".repeat(64), ratings: [] }),
    forgetRatingText: vi.fn().mockResolvedValue(undefined),
    getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile()),
    saveCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile()),
    confirmCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ humanVerified: true })),
    resetCuratedProfile: vi.fn().mockResolvedValue(undefined),
    getProfileCoverage: vi.fn().mockResolvedValue(makeCoverage()),
    getProfileCoverageImages: vi.fn().mockResolvedValue(makeCoverageImages()),
    listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage([])),
    getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail('p1')),
    approveRecutProposal: vi.fn().mockResolvedValue(makeDecision('p1', 'approved')),
    rejectRecutProposal: vi.fn().mockResolvedValue(makeDecision('p1', 'rejected')),
    ...overrides,
  } as MemesApi
}
