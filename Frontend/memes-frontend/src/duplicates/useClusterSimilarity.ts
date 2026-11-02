import { useCallback, useRef, useState } from 'react'
import type { MemesApi } from '../api/MemesApi'
import type { ClusterSimilarityPair } from '../types/generated/all'

export type SimilarityEntry =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ok'; pairs: ClusterSimilarityPair[]; capped: boolean }

// Lazy per-cluster similarity data for the /duplicates page. `load` and `retry` are called from
// event handlers only (never from an effect), so StrictMode's double mount cannot double a request,
// and the ref is touched only inside those handlers.
export function useClusterSimilarity(memesApi: MemesApi) {
  const [entries, setEntries] = useState<Map<number, SimilarityEntry>>(new Map())
  const requestedRef = useRef<Set<number>>(new Set())

  const fetchCluster = useCallback((clusterId: number) => {
    requestedRef.current.add(clusterId)
    setEntries(prev => new Map(prev).set(clusterId, { status: 'loading' }))
    memesApi.getClusterSimilarity(clusterId).then(
      response => setEntries(prev => new Map(prev).set(clusterId, { status: 'ok', pairs: response.pairs, capped: response.capped })),
      () => setEntries(prev => new Map(prev).set(clusterId, { status: 'error' })),
    )
  }, [memesApi])

  const load = useCallback((clusterId: number) => {
    if (requestedRef.current.has(clusterId)) return
    fetchCluster(clusterId)
  }, [fetchCluster])

  const retry = useCallback((clusterId: number) => fetchCluster(clusterId), [fetchCluster])

  return { entries, load, retry }
}
