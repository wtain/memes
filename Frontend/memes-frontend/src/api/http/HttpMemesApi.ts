import { MemesApi } from "../MemesApi"
import { Concept, Meme, MemeSearchRequest, MemeSearchResponse } from "../../types/generated/all"

export class HttpMemesApi implements MemesApi {
  constructor(private readonly baseUrl: string) {}

  async searchMemes(
    request: MemeSearchRequest
  ): Promise<MemeSearchResponse> {
    const params = new URLSearchParams()

    if (request.query) params.set("q", request.query)
    if (request.cursor) params.set("cursor", request.cursor)
    if (request.limit) params.set("limit", String(request.limit))
    if (request.tags?.length) {
      params.set("facets", request.tags.map(tag => {
        return `${tag.category}:${tag.name}`
      }).join(","))
    }

    const response = await fetch(
      `${this.baseUrl}/api/images?${params.toString()}`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }

  async similarMemes(id: string): Promise<MemeSearchResponse> {
    const response = await fetch(
      `${this.baseUrl}/api/images/${id}/similar`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }

  getImageUrl(meme: Meme): string {
    return this.baseUrl + meme.imageUrl;
  }

  async listConcepts(): Promise<Concept[]> {
    const response = await fetch(
      `${this.baseUrl}/api/concepts`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }
  
  async getTopImagesForConcept(conceptId: number): Promise<MemeSearchResponse> {
    const response = await fetch(
      `${this.baseUrl}/api/concepts/top-images?concept_id=${conceptId}`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }

  async getTopConceptsForImage(imageId: string): Promise<Concept[]> {
    const response = await fetch(
      `${this.baseUrl}/api/concepts/for-image?image_id=${imageId}`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Search failed: ${response.status}`)
    }

    return response.json()
  }

  async getMeme(id: string): Promise<Meme> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}`)

    if (!res.ok) throw new Error("Failed to fetch meme")

    return res.json()
  }

  async getConcept(id: number): Promise<Concept> {
    const res = await fetch(`${this.baseUrl}/api/concepts/${id}`)

    if (!res.ok) throw new Error("Failed to fetch concept")

    return res.json()
  }
}
