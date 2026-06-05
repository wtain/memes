import { useEffect, useRef } from 'react'

/**
 * Fetches a resource by ID on mount and whenever the ID changes.
 * Uses a cleanup flag so stale responses from a previous ID (or from
 * React StrictMode's intentional double-mount) never update state.
 *
 * `fetcher` and `onResult` are read via refs, so callers don't need
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
  fetcherRef.current = fetcher
  onResultRef.current = onResult
  onErrorRef.current = onError

  useEffect(() => {
    if (id === undefined) return
    let active = true
    fetcherRef.current(id)
      .then(result => { if (active) onResultRef.current(result) })
      .catch(err => { if (active) onErrorRef.current?.(err) })
    return () => { active = false }
  }, [id])
}
