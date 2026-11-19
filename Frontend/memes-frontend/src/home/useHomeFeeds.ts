import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"

export type FeedStatus = "loading" | "ready" | "error"
export type Feed = { status: FeedStatus; items: Meme[] }

export const RAIL_SIZE = 12
export const MIN_TRENDING = 3

const LOADING: Feed = { status: "loading", items: [] }

/**
 * One feed request per `attempt`. The effect's cleanup marks a run as stale, so a response that
 * arrives after a retry (or after the StrictMode probe mount) is dropped: the latest request wins.
 */
function useFeedRequest(load: () => Promise<Meme[]>, deps: unknown[]) {
  const [feed, setFeed] = useState<Feed>(LOADING)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let stale = false
    load().then(
      items => {
        if (!stale) setFeed({ status: "ready", items })
      },
      () => {
        if (!stale) setFeed({ status: "error", items: [] })
      },
    )
    return () => {
      stale = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, ...deps])

  function retry() {
    setFeed(LOADING)
    setAttempt(a => a + 1)
  }

  return [feed, retry] as const
}

export function useHomeFeeds(memesApi: MemesApi): {
  recent: Feed
  trending: Feed
  retry: (name: "recent" | "trending") => void
} {
  const [recentRaw, retryRecent] = useFeedRequest(
    async () => {
      const response = await memesApi.searchMemes({ limit: RAIL_SIZE, record: false })
      // The unfiltered feed does not exclude flagged images on the server; never show them here.
      return (response.items ?? []).filter(meme => meme.flagged !== true).slice(0, RAIL_SIZE)
    },
    [memesApi],
  )
  const [trendingRaw, retryTrending] = useFeedRequest(
    async () => {
      const response = await memesApi.getTrendingMemes(RAIL_SIZE, undefined, "all")
      return response.items.map(entry => entry.meme)
    },
    [memesApi],
  )

  let trending: Feed
  if (trendingRaw.status === "error") {
    trending = trendingRaw
  } else if (trendingRaw.status === "loading" || recentRaw.status === "loading") {
    // Wait for recent, so a card never shows and then vanishes through the dedupe.
    trending = LOADING
  } else {
    const shown = new Set(recentRaw.status === "ready" ? recentRaw.items.map(m => m.id) : [])
    const items = trendingRaw.items.filter(m => !shown.has(m.id)).slice(0, RAIL_SIZE)
    trending = { status: "ready", items: items.length < MIN_TRENDING ? [] : items }
  }

  return {
    recent: recentRaw,
    trending,
    retry: name => (name === "recent" ? retryRecent() : retryTrending()),
  }
}
