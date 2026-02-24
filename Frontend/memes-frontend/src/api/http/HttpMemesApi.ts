import { MemesApi } from "../MemesApi"
import { MemeSearchRequest, MemeSearchResponse } from "../../types/generated/all"

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
      params.set("facets", request.tags.join(","))
    }

    const response = await fetch(
      `${this.baseUrl}api/images?${params.toString()}`,
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
}
