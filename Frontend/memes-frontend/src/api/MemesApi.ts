import type { Concept, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse } from "../types/generated/all";
import type { TrendEntry, TrendHistoryEntry, TrendsRunDto } from "../types/trends";

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateDuplicates(limit?: number, cursor?: string, threshold?: number): Promise<MemeSearchResponse>;

  iterateExcludedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>;

  similarMemes(id: string): Promise<MemeSearchResponse>

  getImageUrl(meme: Meme): string;

  listConcepts(): Promise<Concept[]>;

  getTopImagesForConcept(conceptId: number): Promise<MemeSearchResponse>;

  getTopConceptsForImage(imageId: string): Promise<Concept[]>;

  getMeme(id: string): Promise<Meme>;

  getConcept(id: number): Promise<Concept>;

  markImageIsExcluded(id: string): Promise<void>;
  
  unmarkImageIsExcluded(id: string): Promise<void>;

  getImageIsExcluded(id: string): Promise<boolean>;

  getTrendsDates(label?: string, name?: string): Promise<string[]>;
  getLatestTrendsRun(date: string): Promise<TrendsRunDto>;
  getTrendsRun(runId: string, minValue?: number): Promise<TrendEntry[]>;
  getTrendsHistory(label: string, name: string): Promise<TrendHistoryEntry[]>;

  uploadMemes(files: File[]): Promise<UploadResponse>;
}
