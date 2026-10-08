import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { TaxonomyResponse } from "./types"

/**
 * Loads the vocabulary once per mount. When a search response reports another taxonomy version
 * (`expectedVersion`), it loads again (the effect re-runs only when the mismatching version changes).
 */
export function useTaxonomy(memesApi: MemesApi, expectedVersion: number | null): TaxonomyResponse | null {
  const [taxonomy, setTaxonomy] = useState<TaxonomyResponse | null>(null)
  const refetchFor = taxonomy && expectedVersion !== null && taxonomy.version !== expectedVersion ? expectedVersion : null

  // The api the vocabulary was last requested for. A satisfied mismatch (refetchFor back to null) must not
  // re-run the load; only the first load, a new api, or a new mismatching version does.
  const loadedForApi = useRef<MemesApi | null>(null)

  useEffect(() => {
    if (loadedForApi.current === memesApi && refetchFor === null) return
    loadedForApi.current = memesApi
    let cancelled = false
    memesApi.getTaxonomy().then(
      (loaded) => { if (!cancelled) setTaxonomy(loaded) },
      () => { /* the chips still render from their keys; only the picker is empty */ },
    )
    return () => { cancelled = true }
  }, [memesApi, refetchFor])

  return taxonomy
}
