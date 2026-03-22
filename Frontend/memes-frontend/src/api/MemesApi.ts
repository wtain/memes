import { Concept, Meme, MemeSearchRequest, MemeSearchResponse } from "../types/generated/all";

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  similarMemes(id: string): Promise<MemeSearchResponse>

  getImageUrl(meme: Meme): string;

  listConcepts(): Promise<Concept[]>;

  getTopImagesForConcept(conceptId: number): Promise<MemeSearchResponse>;

  getTopConceptsForImage(imageId: string): Promise<Concept[]>;

  getMeme(id: string): Promise<Meme>;

  getConcept(id: number): Promise<Concept>;
}
