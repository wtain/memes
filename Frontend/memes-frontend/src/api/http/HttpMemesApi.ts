import type { MemesApi, IngestionTier } from "../MemesApi"
import type {
  Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse,
  TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse,
  IngestionRunStatus, IngestionPendingImage, IngestionCluster, IngestionDecision,
  IngestionResolveResponse, IngestionUndoRejectResponse,
  RunTriggerResponse, RunListResponse, BatchNamesResponse,
  DuplicatePair, DuplicateDismissResponse, DuplicateDecisionListResponse,
} from "../../types/generated/all"

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

  async iterateNoOcrMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse> {
    let paramsString = ""
    if (limit) {
      paramsString += `limit=${limit}&`
    }
    if (cursor) {
      paramsString += `cursor=${cursor}`
    }
    const response = await fetch(
      `${this.baseUrl}/api/images/no-ocr?${paramsString}`,
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

  async iterateDuplicates(limit?: number, cursor?: string, threshold?: number, direction?: "forward" | "backward"): Promise<MemeSearchResponse> {
    const params = new URLSearchParams()
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    if (threshold) params.set("threshold", String(threshold))
    if (direction) params.set("direction", direction)
    const response = await fetch(
      `${this.baseUrl}/api/images/duplicates?${params.toString()}`,
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

  async iterateFlaggedMemes(limit?: number, cursor?: string): Promise<MemeSearchResponse> {
    const params = new URLSearchParams()
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    const response = await fetch(
      `${this.baseUrl}/api/images/flagged?${params.toString()}`,
      { headers: { "Accept": "application/json" } }
    )
    if (!response.ok) throw new Error(`Search failed: ${response.status}`)
    return response.json()
  }

  async similarMemes(id: string, source?: "image" | "description"): Promise<MemeSearchResponse> {
    const params = source ? `?source=${source}` : ""
    const response = await fetch(
      `${this.baseUrl}/api/images/${id}/similar${params}`,
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

  async getDescriptions(id: string): Promise<ImageDescription[]> {
    const response = await fetch(
      `${this.baseUrl}/api/images/${id}/descriptions`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to fetch descriptions: ${response.status}`)
    }

    return response.json()
  }

  async setDescriptionFeedback(imageId: string, promptKey: string, action: "approve" | "reject"): Promise<{ feedback?: string }> {
    const response = await fetch(
      `${this.baseUrl}/api/images/${imageId}/descriptions/${promptKey}/${action}`,
      { method: "PUT", headers: { "Accept": "application/json" } }
    )

    if (!response.ok) {
      throw new Error(`Failed to set description feedback: ${response.status}`)
    }

    return response.json()
  }

  async setDescriptionNote(imageId: string, text: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/images/${imageId}/description-note`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ text }),
    })

    if (!response.ok) {
      throw new Error(`Failed to set description note: ${response.status}`)
    }
  }

  async deleteDescriptionNote(imageId: string): Promise<void> {
    const response = await fetch(`${this.baseUrl}/api/images/${imageId}/description-note`, {
      method: "DELETE",
      headers: { "Accept": "application/json" },
    })

    if (!response.ok) {
      throw new Error(`Failed to delete description note: ${response.status}`)
    }
  }

  getImageUrl(meme: Meme): string {
    return this.baseUrl + meme.imageUrl;
  }

  getImageUrlById(imageId: string): string {
    return `${this.baseUrl}/api/images/${imageId}`
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

  async markImageIsFlagged(id: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/mark_flagged`, {
      method: "PUT"
    })

    if (!res.ok) throw new Error("Failed mark as flagged")
  }

  async unmarkImageIsFlagged(id: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/unmark_flagged`, {
      method: "PUT"
    })

    if (!res.ok) throw new Error("Failed unmark as flagged")
  }

  async getImageIsFlagged(id: string): Promise<boolean> {
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/get_flagged`)

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

  async getLatestTrendsRun(date: string): Promise<TrendsRun> {
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

  async getIngestionRunStatus(): Promise<IngestionRunStatus | null> {
    const res = await fetch(`${this.baseUrl}/api/ingestion/run`, { headers: { Accept: "application/json" } })
    if (res.status === 404) return null // no active ingestion run -- not an error
    if (!res.ok) throw new Error(`Failed to fetch ingestion run status: ${res.status}`)
    return res.json()
  }

  async getIngestionPending(): Promise<IngestionPendingImage[]> {
    const res = await fetch(`${this.baseUrl}/api/ingestion/pending`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch pending images: ${res.status}`)
    return res.json()
  }

  async getIngestionClusters(tier: IngestionTier): Promise<IngestionCluster[]> {
    const res = await fetch(`${this.baseUrl}/api/ingestion/clusters/${tier}`, { headers: { Accept: "application/json" } })
    if (!res.ok) throw new Error(`Failed to fetch ${tier} clusters: ${res.status}`)
    return res.json()
  }

  async resolveIngestionCluster(tier: IngestionTier, decisions: IngestionDecision[]): Promise<IngestionResolveResponse> {
    const res = await fetch(`${this.baseUrl}/api/ingestion/clusters/${tier}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ decisions }),
    })
    if (!res.ok) throw new Error(`Failed to resolve ${tier} decisions: ${res.status}`)
    return res.json()
  }

  async undoIngestionReject(imageId: string): Promise<IngestionUndoRejectResponse> {
    const res = await fetch(`${this.baseUrl}/api/ingestion/images/${imageId}/undo-reject`, {
      method: "POST",
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to undo reject: ${res.status}`)
    return res.json()
  }

  async dismissDuplicateCluster(clusterId: number): Promise<DuplicateDismissResponse> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/clusters/${clusterId}/dismiss`, {
      method: "POST",
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to dismiss cluster ${clusterId}: ${res.status}`)
    return res.json()
  }

  async undoDismissDuplicates(pairs: DuplicatePair[]): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/pairs/undo-dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ pairs }),
    })
    if (!res.ok) throw new Error(`Failed to undo dismiss: ${res.status}`)
  }

  async listDuplicateDecisions(limit = 20, offset = 0): Promise<DuplicateDecisionListResponse> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/decisions?${params.toString()}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to list duplicate decisions: ${res.status}`)
    return res.json()
  }

  async listBatchNames(): Promise<BatchNamesResponse> {
    const res = await fetch(`${this.baseUrl}/api/admin/batches/names`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch batch names: ${res.status}`)
    return res.json()
  }

  async triggerBatchRun(batchName: string): Promise<RunTriggerResponse> {
    const res = await fetch(`${this.baseUrl}/api/admin/batches/${batchName}/run`, {
      method: "POST",
      headers: { Accept: "application/json" },
    })
    if (res.status === 409) throw new Error(`${batchName} is already running`)
    if (res.status === 404) throw new Error(`${batchName} is not a recognized batch`)
    if (!res.ok) throw new Error(`Failed to trigger ${batchName}: ${res.status}`)
    return res.json()
  }

  async listBatchRuns(limit?: number, offset?: number): Promise<RunListResponse> {
    const params = new URLSearchParams()
    if (limit !== undefined) params.set("limit", String(limit))
    if (offset !== undefined) params.set("offset", String(offset))
    const qs = params.toString()
    const res = await fetch(`${this.baseUrl}/api/admin/batches/runs${qs ? `?${qs}` : ""}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch batch runs: ${res.status}`)
    return res.json()
  }
}
