import { useEffect, useRef } from 'react'

/**
 * Fetches a resource by ID on mount and whenever the ID changes.
 * Ref-guard skips re-fetching the same ID (prevents StrictMode double-request).
 * Cleanup flag ensures stale results are never applied.
 *
 * `fetcher` and `onResult` are read via refs so callers don't need
 * useCallback — passing inline arrow functions is fine.
 */
export function useFetchById<ID, Result>(
  id: ID | undefined,
  fetcher: (id: ID) => Promise<Result>,
  onResult: (result: Result) => void,
  onError?: (error: unknown) => void,
): void {
  const fetcherRef = useRef(fetcher)
  const onResultRef = useRef(onResult)
  const onErrorRef = useRef(onError)
  // lastFetchedId: skip re-fetching the same ID (prevents StrictMode double-request)
  // currentId: invalidate results from a previous ID when id changes
  const lastFetchedIdRef = useRef<ID | undefined>(undefined)
  const currentIdRef = useRef<ID | undefined>(undefined)

  // Sync refs after every render without mutating during render
  useEffect(() => {
    fetcherRef.current = fetcher
    onResultRef.current = onResult
    onErrorRef.current = onError
  })

  useEffect(() => {
    if (id === undefined) return
    if (Object.is(lastFetchedIdRef.current, id)) return
    lastFetchedIdRef.current = id
    currentIdRef.current = id
    fetcherRef.current(id)
      .then(result => { if (Object.is(currentIdRef.current, id)) onResultRef.current(result) })
      .catch(err => { if (Object.is(currentIdRef.current, id)) onErrorRef.current?.(err) })
  }, [id])
}
