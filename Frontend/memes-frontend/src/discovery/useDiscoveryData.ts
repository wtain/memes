import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { DiscoveryRequestError } from "./types"

type View = "memes" | "queries" | "misses"
type State<T> = { key: string; data: T | null; error: string | null }

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}

export function useDiscoveryData<T>(memesApi: MemesApi, view: View, params: object) {
  const key = JSON.stringify([view, params])
  const [reloadKey, setReloadKey] = useState(0)
  const [state, setState] = useState<State<T> | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false
    const [, current] = JSON.parse(key) as [View, never]
    const request: Promise<unknown> = view === "memes"
      ? memesApi.getDiscoveryMemes(current, controller.signal)
      : view === "queries" ? memesApi.getDiscoveryQueries(current, controller.signal)
      : memesApi.getDiscoveryMisses(current, controller.signal)
    request.then(
      (data) => { if (!cancelled) setState({ key, data: data as T, error: null }) },
      (error) => {
        if (cancelled || isAbort(error)) return
        setState({ key, data: null, error: error instanceof DiscoveryRequestError && error.detail ? error.detail : "Request failed" })
      },
    )
    return () => { cancelled = true; controller.abort() }
  }, [memesApi, view, key, reloadKey])

  const current = state !== null && state.key === key ? state : null
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: current === null,
    retry: () => { setState(null); setReloadKey((n) => n + 1) },
  }
}
