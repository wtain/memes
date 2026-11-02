export type MemeSort = "per_100" | "redrops" | "redrops_window" | "popularity" | "saves" | "impressions"
export type QuerySort = "times" | "last" | "min_results"
export type MissSort = "times" | "explicit" | "last"
export type Kind = "search" | "meme_text" | "all"

export type MemesParams = {
  windowDays?: number; sources?: string[]; minRedrops?: number; maxImpressions?: number; sort?: MemeSort; limit?: number
}
export type QueriesParams = {
  kind?: Kind; maxResults?: number; includeDegraded?: boolean; sinceDays?: number; minTimes?: number; sort?: QuerySort; limit?: number
}
export type MissesParams = {
  gapMinutes?: number; kind?: Kind; sinceDays?: number; minTimes?: number; explicitOnly?: boolean; sort?: MissSort; limit?: number
}

export class DiscoveryRequestError extends Error {
  readonly status: number
  readonly detail: string
  constructor(status: number, detail: string) {
    super(detail || `Request failed: ${status}`)
    this.name = "DiscoveryRequestError"
    this.status = status
    this.detail = detail
  }
}

export type { DiscoveryMemes, DiscoveryQueries, DiscoveryMisses } from "../types/generated/all"
