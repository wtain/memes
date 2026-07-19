import type { Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse, TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse } from "../types/generated/all";

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateDuplicates(limit?: number, cursor?: string, threshold?: number): Promise<MemeSearchResponse>;

  iterateFlaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>;

  similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse>

  getDescriptions(id: string): Promise<ImageDescription[]>

  setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }>

  getImageUrl(meme: Meme): string;

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
}
