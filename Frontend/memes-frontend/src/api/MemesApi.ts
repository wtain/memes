import type {
  Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse,
  TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse,
  IngestionRunStatus, IngestionPendingImage, IngestionClusterPage, IngestionDecision,
  IngestionResolveResponse, IngestionUndoRejectResponse, IngestionTierBReviewPage,
  RunTriggerResponse, RunListResponse, BatchNamesResponse, BatchMetricsResponse,
  DuplicatePair, DuplicateDismissResponse, DuplicateDecisionListResponse, ClusterSimilarityResponse,
  MemeFromTextRequest, MemeFromTextResponse, TaxonomyResponse,
  RatingRequest, RatingResponse, RatingsResponse,
  CuratedProfile, CuratedProfileSaveRequest, ProfileCoverage, ProfileCoverageImages,
  RecutDecision, RecutProposalDetail, RecutProposalPage, MemePopularity,
} from "../types/generated/all";
import type { Category } from "../curator/types";

export type IngestionTier = "tier_a" | "tier_b"

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  searchMemeFromText(request: MemeFromTextRequest, signal?: AbortSignal): Promise<MemeFromTextResponse>

  getTaxonomy(): Promise<TaxonomyResponse>

  iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateDuplicates(limit?: number, cursor?: string, threshold?: number, direction?: "forward" | "backward"): Promise<MemeSearchResponse>;

  dismissDuplicateCluster(clusterId: number, memberIds?: string[]): Promise<DuplicateDismissResponse>;

  getClusterSimilarity(clusterId: number): Promise<ClusterSimilarityResponse>;

  undoDismissDuplicates(pairs: DuplicatePair[]): Promise<void>;

  listDuplicateDecisions(limit?: number, offset?: number): Promise<DuplicateDecisionListResponse>;

  iterateFlaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>;

  similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse>

  getDescriptions(id: string): Promise<ImageDescription[]>

  getMemePopularity(id: string): Promise<MemePopularity>

  setMemePopularity(id: string, popularity: number): Promise<MemePopularity>

  setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }>

  setDescriptionNote(imageId: string, text: string): Promise<void>

  deleteDescriptionNote(imageId: string): Promise<void>

  getImageUrl(meme: Meme): string;

  getImageUrlById(imageId: string): string;

  listConcepts(): Promise<Concept[]>;

  getTopImagesForConcept(conceptId: number): Promise<MemeSearchResponse>;

  getTopConceptsForImage(imageId: string): Promise<Concept[]>;

  getMeme(id: string): Promise<Meme>;

  getConcept(id: number): Promise<Concept>;

  markImageIsFlagged(id: string, reason?: string, duplicateOf?: string): Promise<void>;

  unmarkImageIsFlagged(id: string): Promise<void>;

  getImageIsFlagged(id: string): Promise<boolean>;

  getRecommendations(q?: string, limit?: number, cursor?: string): Promise<MemeSearchResponse>;

  getTrendsDates(label?: string, name?: string): Promise<string[]>;
  getLatestTrendsRun(date: string): Promise<TrendsRun>;
  getTrendsRun(runId: string, minValue?: number): Promise<TrendEntry[]>;
  getTrendsHistory(label: string, name: string): Promise<TrendHistoryEntry[]>;

  uploadMemes(files: File[]): Promise<UploadResponse>;

  getStatistics(options?: { live?: boolean }): Promise<StatisticsResponse>;

  /** Resolves to null (not a rejection) when no ingestion run is currently in progress. */
  getIngestionRunStatus(): Promise<IngestionRunStatus | null>;
  getIngestionPending(): Promise<IngestionPendingImage[]>;
  getIngestionClusters(tier: IngestionTier, cursor?: string): Promise<IngestionClusterPage>;
  getIngestionTierBReview(cursor?: string): Promise<IngestionTierBReviewPage>;
  resolveIngestionCluster(tier: IngestionTier, decisions: IngestionDecision[]): Promise<IngestionResolveResponse>;
  undoIngestionReject(imageId: string): Promise<IngestionUndoRejectResponse>;

  listBatchNames(): Promise<BatchNamesResponse>;
  triggerBatchRun(batchName: string): Promise<RunTriggerResponse>;
  listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse>;
  getBatchMetrics(process: string, limit?: number): Promise<BatchMetricsResponse>;

  putRating(request: RatingRequest): Promise<RatingResponse>;
  deleteRating(queryHash: string, imageId: string): Promise<void>;
  getRatings(queryHash: string): Promise<RatingsResponse>;
  forgetRatingText(queryHash: string): Promise<void>;

  getCuratedProfile(imageId: string): Promise<CuratedProfile>;
  saveCuratedProfile(imageId: string, request: CuratedProfileSaveRequest): Promise<CuratedProfile>;
  confirmCuratedProfile(imageId: string): Promise<CuratedProfile>;
  resetCuratedProfile(imageId: string): Promise<void>;
  getProfileCoverage(): Promise<ProfileCoverage>;
  getProfileCoverageImages(category: Category, cursor?: string, limit?: number): Promise<ProfileCoverageImages>;
  listRecutProposals(offset?: number, limit?: number): Promise<RecutProposalPage>
  getRecutProposal(id: string): Promise<RecutProposalDetail>
  approveRecutProposal(id: string): Promise<RecutDecision>
  rejectRecutProposal(id: string): Promise<RecutDecision>
}
