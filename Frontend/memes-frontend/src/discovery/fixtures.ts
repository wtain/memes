import type { DiscoveryMemes, DiscoveryQueries, DiscoveryMisses } from "./types"

type MemeItem = DiscoveryMemes["items"][number]
type QueryItem = DiscoveryQueries["items"][number]
type MissItem = DiscoveryMisses["items"][number]

export function makeMemeItem(overrides: Partial<MemeItem> = {}): MemeItem {
  return {
    id: "m1", imageUrl: "/api/images/m1", filename: "quokka.jpg", redrops: 3, redropsWindow: 2,
    sources: [{ source: "hash_cross_corpus", count: 3 }], popularity: 4, saves: 1, impressions: 10,
    impressionsWindow: 5, lastBack: "2026-11-01T12:30:00Z", per100: 40, per100Infinite: false, ...overrides,
  }
}

export function makeDiscoveryMemes(overrides: Partial<DiscoveryMemes> = {}): DiscoveryMemes {
  return {
    windowDays: 30, sources: ["hash_cross_corpus"], impressionsAvailable: true, items: [makeMemeItem()], ...overrides,
  }
}

export function makeQueryItem(overrides: Partial<QueryItem> = {}): QueryItem {
  return {
    query: "funny cat", tags: ["topic:work"], kind: "search", times: 2, firstAt: "2026-10-30T09:00:00Z",
    lastAt: "2026-11-01T10:00:00Z", minResults: 0, avgResults: 0.5, web: 1, android: 1, degraded: 0, fallbacks: [],
    ...overrides,
  }
}

export function makeDiscoveryQueries(items: QueryItem[] = [makeQueryItem()]): DiscoveryQueries {
  return { kind: "search", items }
}

export function makeMissItem(overrides: Partial<MissItem> = {}): MissItem {
  return {
    query: "lost quokka", tags: [], kind: "search", times: 3, explicit: 1, searches: 2,
    firstAt: "2026-10-30T09:00:00Z", lastAt: "2026-11-01T10:00:00Z", web: 1, android: 2, ...overrides,
  }
}

export function makeDiscoveryMisses(overrides: Partial<DiscoveryMisses> = {}): DiscoveryMisses {
  return {
    status: "ok", cutoff: "2026-09-01T00:00:00Z", gapMinutes: 5, kind: "search", items: [makeMissItem()], ...overrides,
  }
}
