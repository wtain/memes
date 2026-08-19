import type {
  Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse,
  TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse,
  IngestionRunStatus, IngestionPendingImage, IngestionCluster, IngestionDecision,
  IngestionResolveResponse, IngestionUndoRejectResponse,
  RunTriggerResponse, RunListResponse, BatchNamesResponse,
  DuplicatePair, DuplicateDismissResponse, DuplicateDecisionListResponse,
} from "../types/generated/all";

export type IngestionTier = "tier_a" | "tier_b"

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateDuplicates(limit?: number, cursor?: string, threshold?: number, direction?: "forward" | "backward"): Promise<MemeSearchResponse>;

  dismissDuplicateCluster(clusterId: number): Promise<DuplicateDismissResponse>;

  undoDismissDuplicates(pairs: DuplicatePair[]): Promise<void>;

  listDuplicateDecisions(limit?: number, offset?: number): Promise<DuplicateDecisionListResponse>;

  iterateFlaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>;

  similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse>

  getDescriptions(id: string): Promise<ImageDescription[]>

  setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }>

  getImageUrl(meme: Meme): string;

  getImageUrlById(imageId: string): string;

  listConcepts(): Promise<Concept[]>;

  getTopImagesForConcept(conceptId: number): Promise<MemeSearchResponse>;

  getTopConceptsForImage(imageId: string): Promise<Concept[]>;

  getMeme(id: string): Promise<Meme>;

  getConcept(id: number): Promise<Concept>;

  markImageIsFlagged(id: string): Promise<void>;

  unmarkImageIsFlagged(id: string): Promise<void>;

  getImageIsFlagged(id: string): Promise<boolean>;

  getRecommendations(q?: string, limit?: number, cursor?: string): Promise<MemeSearchResponse>;

  getTrendsDates(label?: string, name?: string): Promise<string[]>;
  getLatestTrendsRun(date: string): Promise<TrendsRun>;
  getTrendsRun(runId: string, minValue?: number): Promise<TrendEntry[]>;
  getTrendsHistory(label: string, name: string): Promise<TrendHistoryEntry[]>;

  uploadMemes(files: File[]): Promise<UploadResponse>;

  getStatistics(): Promise<StatisticsResponse>;

  /** Resolves to null (not a rejection) when no ingestion run is currently in progress. */
  getIngestionRunStatus(): Promise<IngestionRunStatus | null>;
  getIngestionPending(): Promise<IngestionPendingImage[]>;
  getIngestionClusters(tier: IngestionTier): Promise<IngestionCluster[]>;
  resolveIngestionCluster(tier: IngestionTier, decisions: IngestionDecision[]): Promise<IngestionResolveResponse>;
  undoIngestionReject(imageId: string): Promise<IngestionUndoRejectResponse>;

  listBatchNames(): Promise<BatchNamesResponse>;
  triggerBatchRun(batchName: string): Promise<RunTriggerResponse>;
  listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse>;
}
