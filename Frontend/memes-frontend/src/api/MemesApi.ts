import { MemeSearchRequest, MemeSearchResponse } from "../types/generated/all";

export interface MemesApi {
  searchMemes(request: MemeSearchRequest): Promise<MemeSearchResponse>

  similarMemes(id: string): Promise<MemeSearchResponse>
}
