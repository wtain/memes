import type { MemesApi, IngestionTier } from "../MemesApi"
import type {
  Concept, ImageDescription, Meme, MemeSearchRequest, MemeSearchResponse, UploadResponse,
  TrendEntry, TrendHistoryEntry, TrendsRun, StatisticsResponse,
  IngestionRunStatus, IngestionPendingImage, IngestionClusterPage, IngestionDecision,
  IngestionResolveResponse, IngestionUndoRejectResponse, IngestionTierBReviewPage,
  RunTriggerResponse, RunListResponse, BatchNamesResponse, BatchMetricsResponse,
  DuplicatePair, DuplicateDismissResponse, DuplicateDecisionListResponse, ClusterSimilarityResponse,
  MemeFromTextRequest, MemeFromTextResponse, TaxonomyResponse,
  RatingRequest, RatingResponse, RatingsResponse,
  CuratedProfile, CuratedProfileSaveRequest, ProfileCoverage, ProfileCoverageImages,
  RecutAdjustRequest, RecutDecision, RecutProposalDetail, RecutProposalPage, RecutStats, MemePopularity, MemePopularityUpdate, MemeSaves, MemeImpressions, TrendingMemesResponse,
} from "../../types/generated/all"
import type { Category } from "../../curator/types"
import { MessageSearchFailedError, MessageSearchValidationError, RatingFailedError, CuratorRequestError } from "../errors"

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

  async searchMemeFromText(request: MemeFromTextRequest, signal?: AbortSignal): Promise<MemeFromTextResponse> {
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/search`, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    })

    if (response.status === 422) {
      throw new MessageSearchValidationError(await readValidationMessages(response))
    }
    if (!response.ok) {
      throw new MessageSearchFailedError(response.status)
    }
    return response.json()
  }

  async getTaxonomy(): Promise<TaxonomyResponse> {
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/taxonomy`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) {
      throw new Error(`Taxonomy failed: ${response.status}`)
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

  async getTrendingMemes(limit?: number, cursor?: string, window?: "all" | "30d"): Promise<TrendingMemesResponse> {
    const params = new URLSearchParams()
    if (limit) params.set("limit", String(limit))
    if (cursor) params.set("cursor", cursor)
    if (window === "30d") params.set("window", window)
    const query = params.toString()
    const response = await fetch(
      `${this.baseUrl}/api/images/trending${query ? `?${query}` : ""}`,
      { headers: { "Accept": "application/json" } },
    )
    if (!response.ok) {
      throw new Error(`Trending memes failed: ${response.status}`)
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

  async getMemePopularity(id: string): Promise<MemePopularity> {
    const response = await fetch(
      `${this.baseUrl}/api/images/meme/${id}/popularity`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to fetch popularity: ${response.status}`)
    }

    return response.json()
  }

  async setMemePopularity(id: string, popularity: number): Promise<MemePopularity> {
    const body: MemePopularityUpdate = { popularity }
    const response = await fetch(
      `${this.baseUrl}/api/images/meme/${id}/popularity`,
      {
        method: "PUT",
        headers: {
          "Accept": "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to set popularity: ${response.status}`)
    }

    return response.json()
  }

  async getMemeSaves(id: string): Promise<MemeSaves> {
    const response = await fetch(
      `${this.baseUrl}/api/images/meme/${id}/saves`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to fetch saves: ${response.status}`)
    }

    return response.json()
  }

  async getMemeImpressions(id: string): Promise<MemeImpressions> {
    const response = await fetch(
      `${this.baseUrl}/api/images/meme/${id}/impressions`,
      {
        headers: {
          "Accept": "application/json",
        },
      }
    )

    if (!response.ok) {
      throw new Error(`Failed to fetch impressions: ${response.status}`)
    }

    return response.json()
  }

  async reportNotFound(searchId: string): Promise<void> {
    const response = await fetch(
      `${this.baseUrl}/api/history/${encodeURIComponent(searchId)}/not-found`,
      { method: "POST" }
    )

    if (!response.ok) {
      throw new Error(`Failed to report: ${response.status}`)
    }
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

  async markImageIsFlagged(id: string, reason?: string, duplicateOf?: string): Promise<void> {
    const query = new URLSearchParams()
    if (reason) query.set("reason", reason)
    if (duplicateOf) query.set("duplicateOf", duplicateOf)
    const params = query.toString() !== "" ? `?${query.toString()}` : ""
    const res = await fetch(`${this.baseUrl}/api/images/meme/${id}/mark_flagged${params}`, {
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

  async getStatistics(options?: { live?: boolean }): Promise<StatisticsResponse> {
    const query = options?.live ? "?live=true" : ""
    const res = await fetch(`${this.baseUrl}/api/diagnostics/statistics${query}`, { headers: { Accept: "application/json" } })
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

  async getIngestionClusters(tier: IngestionTier, cursor?: string): Promise<IngestionClusterPage> {
    const params = new URLSearchParams({ limit: "40" })
    if (cursor) params.set("cursor", cursor)
    const res = await fetch(`${this.baseUrl}/api/ingestion/clusters/${tier}?${params}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch ${tier} clusters: ${res.status}`)
    return res.json()
  }

  async getIngestionTierBReview(cursor?: string): Promise<IngestionTierBReviewPage> {
    const params = new URLSearchParams({ limit: "40" })
    if (cursor) params.set("cursor", cursor)
    const res = await fetch(`${this.baseUrl}/api/ingestion/review/tier_b?${params}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch tier B review: ${res.status}`)
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

  async dismissDuplicateCluster(clusterId: number, memberIds?: string[]): Promise<DuplicateDismissResponse> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/clusters/${clusterId}/dismiss`, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ member_ids: memberIds ?? null }),
    })
    if (!res.ok) throw new Error(`Failed to dismiss cluster ${clusterId}: ${res.status}`)
    return res.json()
  }

  async getClusterSimilarity(clusterId: number): Promise<ClusterSimilarityResponse> {
    const res = await fetch(`${this.baseUrl}/api/images/duplicates/clusters/${clusterId}/similarity`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch similarity for cluster ${clusterId}: ${res.status}`)
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

  async getBatchMetrics(process: string, limit?: number): Promise<BatchMetricsResponse> {
    const params = new URLSearchParams({ process })
    if (limit !== undefined) params.set("limit", String(limit))
    const res = await fetch(`${this.baseUrl}/api/admin/batches/metrics?${params.toString()}`, {
      headers: { Accept: "application/json" },
    })
    if (!res.ok) throw new Error(`Failed to fetch batch metrics: ${res.status}`)
    return res.json()
  }

  async putRating(request: RatingRequest): Promise<RatingResponse> {
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings`, {
      method: "PUT",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    })
    if (!response.ok) throw new RatingFailedError(response.status)
    return response.json()
  }

  async deleteRating(queryHash: string, imageId: string): Promise<void> {
    const params = new URLSearchParams({ queryHash, imageId })
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings?${params.toString()}`, { method: "DELETE" })
    if (!response.ok) throw new RatingFailedError(response.status)
  }

  async getRatings(queryHash: string): Promise<RatingsResponse> {
    const params = new URLSearchParams({ queryHash })
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings?${params.toString()}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new RatingFailedError(response.status)
    return response.json()
  }

  async forgetRatingText(queryHash: string): Promise<void> {
    const params = new URLSearchParams({ queryHash })
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/ratings/text?${params.toString()}`, { method: "DELETE" })
    if (!response.ok) throw new RatingFailedError(response.status)
  }

  async getCuratedProfile(imageId: string): Promise<CuratedProfile> {
    const encodedId = encodeURIComponent(imageId)
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/profiles/${encodedId}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async saveCuratedProfile(imageId: string, request: CuratedProfileSaveRequest): Promise<CuratedProfile> {
    const encodedId = encodeURIComponent(imageId)
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/profiles/${encodedId}`, {
      method: "PUT",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(request),
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async confirmCuratedProfile(imageId: string): Promise<CuratedProfile> {
    const encodedId = encodeURIComponent(imageId)
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/profiles/${encodedId}/confirm`, {
      method: "POST",
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async resetCuratedProfile(imageId: string): Promise<void> {
    const encodedId = encodeURIComponent(imageId)
    const response = await fetch(`${this.baseUrl}/api/meme-from-text/profiles/${encodedId}`, {
      method: "DELETE",
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
  }

  async getProfileCoverage(): Promise<ProfileCoverage> {
    const response = await fetch(`${this.baseUrl}/api/admin/profile-coverage`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async getProfileCoverageImages(category: Category, cursor?: string, limit?: number): Promise<ProfileCoverageImages> {
    const params = new URLSearchParams()
    params.set("category", category)
    if (cursor !== undefined) params.set("cursor", cursor)
    if (limit !== undefined) params.set("limit", String(limit))
    const response = await fetch(`${this.baseUrl}/api/admin/profile-coverage/images?${params.toString()}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async listRecutProposals(offset?: number, limit?: number): Promise<RecutProposalPage> {
    const params = new URLSearchParams()
    if (offset !== undefined) params.set("offset", String(offset))
    if (limit !== undefined) params.set("limit", String(limit))
    const query = params.toString()
    const response = await fetch(`${this.baseUrl}/api/recut/proposals${query ? `?${query}` : ""}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async getRecutProposal(id: string): Promise<RecutProposalDetail> {
    const response = await fetch(`${this.baseUrl}/api/recut/proposals/${encodeURIComponent(id)}`, {
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  async getRecutStats(): Promise<RecutStats> {
    const response = await fetch(`${this.baseUrl}/api/recut/stats`, { headers: { "Accept": "application/json" } })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }

  approveRecutProposal(id: string): Promise<RecutDecision> {
    return this.decideRecutProposal(id, "approve")
  }

  rejectRecutProposal(id: string): Promise<RecutDecision> {
    return this.decideRecutProposal(id, "reject")
  }

  async adjustRecutProposal(id: string, box: RecutAdjustRequest): Promise<RecutProposalDetail> {
    const response = await fetch(`${this.baseUrl}/api/recut/proposals/${encodeURIComponent(id)}/adjust`, {
      method: "POST",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ x: box.x, y: box.y, w: box.w, h: box.h }),
    })
    if (!response.ok) throw new CuratorRequestError(response.status, await readDetailCode(response))
    return response.json()
  }

  private async decideRecutProposal(id: string, verb: "approve" | "reject"): Promise<RecutDecision> {
    const response = await fetch(`${this.baseUrl}/api/recut/proposals/${encodeURIComponent(id)}/${verb}`, {
      method: "POST",
      headers: { "Accept": "application/json" },
    })
    if (!response.ok) throw new CuratorRequestError(response.status, await readDetailCode(response))
    return response.json()
  }
}

/** The reason code of a 409 (FastAPI's string `detail`); undefined when the body has none. */
async function readDetailCode(response: Response): Promise<string | undefined> {
  try {
    const detail = (await response.json())?.detail
    return typeof detail === "string" ? detail : undefined
  } catch {
    return undefined
  }
}

/** Server messages only: FastAPI's 422 items also echo the rejected `input`, which must never reach the UI. */
async function readValidationMessages(response: Response): Promise<string[]> {
  try {
    const body = await response.json()
    const detail = body?.detail
    if (!Array.isArray(detail)) return typeof detail === "string" ? [detail] : []
    return detail
      .map((item: unknown) => (typeof item === "string" ? item : (item as { msg?: unknown })?.msg))
      .filter((message): message is string => typeof message === "string")
  } catch {
    return []
  }
}
