import type { MemesApi } from "../MemesApi"
import type { Concept, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse } from "../../types/generated/all"
import type { TrendEntry, TrendHistoryEntry, TrendsRunDto } from "../../types/trends"
import type { StatisticsResponse } from "../../types/statistics"

export class HttpMemesApi implements MemesApi {
  private readonly baseUrl: string
  constructor(baseUrl: string) { this.baseUrl = baseUrl }

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

  async iterateUntaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse> {
    let paramsString = ""
    if (limit) {
      paramsString += `limit=${limit}&`
    }
    if (cursor) {
      paramsString += `cursor=${cursor}`
    }
    const response = await fetch(
      `${this.baseUrl}/api/images/untagged?${paramsString}`,
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

  async iterateDuplicates(limit?: number, cursor?: string, threshold?: number): Promise<MemeSearchResponse> {
    let paramsString = ""
    if (limit) {
      paramsString += `limit=${limit}&`
    }
    if (cursor) {
      paramsString += `cursor=${cursor}&`
    }
    if (threshold) {
      paramsString += `threshold=${threshold}`
    }
    const response = await fetch(
      `${this.baseUrl}/api/images/duplicates?${paramsString}`,
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

  async iterateExcludedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse> {
    const params = new URLSearchParams()
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    const response = await fetch(
      `${this.baseUrl}/api/images/excluded?${params.toString()}`,
      { headers: { "Accept": "application/json" } }
    )
    if (!response.ok) throw new Error(`Search failed: ${response.status}`)
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

  async markImageIsExcluded(id: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/mark_excluded`, {
      method: "PUT"
    })

    if (!res.ok) throw new Error("Failed mark as excluded")
  }

  async unmarkImageIsExcluded(id: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/unmark_excluded`, {
      method: "PUT"
    })

    if (!res.ok) throw new Error("Failed unmark as excluded")
  }

  async getImageIsExcluded(id: string): Promise<boolean> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/get_excluded`)

    if (!res.ok) throw new Error("Failed to fetch image extras")

    return Promise.resolve(Number.parseInt(await res.text()) === 1)
  }

  async getRecommendations(q?: string, limit?: number, cursor?: string): Promise<MemeSearchResponse> {
    const params = new URLSearchParams()
    if (q) params.set("q", q)
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    const res = await fetch(`${this.baseUrl}/api/recommendations?${params}`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch recommendations: ${res.status}`)
    return res.json()
  }

  async getTrendsDates(label?: string, name?: string): Promise<string[]> {
    const params = new URLSearchParams()
    if (label) params.set("label", label)
    if (name) params.set("name", name)
    const res = await fetch(`${this.baseUrl}/api/trends/dates?${params}`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch trend dates: ${res.status}`)
    return res.json()
  }

  async getLatestTrendsRun(date: string): Promise<TrendsRunDto> {
    const res = await fetch(`${this.baseUrl}/api/trends/dates/${date}/runs/latest`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch latest trends run: ${res.status}`)
    return res.json()
  }

  async getTrendsRun(runId: string, minValue?: number): Promise<TrendEntry[]> {
    const params = new URLSearchParams()
    if (minValue !== undefined) params.set("min_value", String(minValue))
    const res = await fetch(`${this.baseUrl}/api/trends/runs/${runId}?${params}`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch trends run: ${res.status}`)
    return res.json()
  }

  async getTrendsHistory(label: string, name: string): Promise<TrendHistoryEntry[]> {
    const params = new URLSearchParams({ label, name })
    const res = await fetch(`${this.baseUrl}/api/trends/history?${params}`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch trends history: ${res.status}`)
    return res.json()
  }

  async uploadMemes(files: File[]): Promise<UploadResponse> {
    const form = new FormData()
    for (const file of files) form.append("files", file)
    const res = await fetch(`${this.baseUrl}/api/uploads`, { method: "POST", body: form })
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`)
    return res.json()
  }

  async getStatistics(): Promise<StatisticsResponse> {
    const res = await fetch(`${this.baseUrl}/api/diagnostics/statistics`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch statistics: ${res.status}`)
    return res.json()
  }
}
