import { Concept, Meme, MemeSearchRequest, MemeSearchResponse } from "../types/generated/all";

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse>

  iterateDuplicates(limit?: number, cursor?: string, threshold?: number): Promise<MemeSearchResponse>;

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
}
