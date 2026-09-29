import type { StatisticsResponse } from "../types/generated/all"

function lerpGroup<G extends object>(from: G, to: G, t: number): G {
  const source = from as Record<string, unknown>
  const result: Record<string, unknown> = {}
  for (const [key, target] of Object.entries(to)) {
    const start = source[key]
    result[key] = typeof target === "number" && typeof start === "number"
      ? Math.round(start + (target - start) * t)
      : target
  }
  return result as G
}

export function lerpStats(from: StatisticsResponse, to: StatisticsResponse, t: number): StatisticsResponse {
  return {
    ...to,
    memes: lerpGroup(from.memes, to.memes, t),
    content: lerpGroup(from.content, to.content, t),
    trends: lerpGroup(from.trends, to.trends, t),
  }
}
