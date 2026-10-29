import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { RecutStats } from "./types"

/** The review statistics. `refreshKey` null means "not yet" (nothing is requested); a new key requests them again.
 * A failed request leaves no statistics: the table is optional and must never block reviewing. */
export function useRecutStats(memesApi: MemesApi, refreshKey: number | null): RecutStats | null {
  const [stats, setStats] = useState<RecutStats | null>(null)

  useEffect(() => {
    if (refreshKey === null) return
    let cancelled = false
    memesApi.getRecutStats().then(
      (loaded) => { if (!cancelled) setStats(loaded) },
      () => { if (!cancelled) setStats(null) },
    )
    return () => { cancelled = true }
  }, [memesApi, refreshKey])

  return stats
}
