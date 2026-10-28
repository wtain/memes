import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { CuratorRequestError } from "../api/errors"
import type { RecutProposal, RecutProposalDetail } from "./types"

export const PAGE_SIZE = 50

type ListState = { items: RecutProposal[]; total: number; status: "loading" | "ready" | "failed" }
export type DetailState =
  | { status: "ready"; data: RecutProposalDetail }
  | { status: "missing" }
  | { status: "failed" }
type Loaded = { id: string; state: DetailState }

const LOADING: ListState = { items: [], total: 0, status: "loading" }

export function useRecutQueue(memesApi: MemesApi, openId: string | null) {
  const [list, setList] = useState<ListState>(LOADING)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Only touched in handlers and effects: one next-page request at a time.
  const loadingMoreRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    memesApi.listRecutProposals(0, PAGE_SIZE).then(
      (page) => { if (!cancelled) setList({ items: page.items, total: page.total, status: "ready" }) },
      () => { if (!cancelled) setList({ items: [], total: 0, status: "failed" }) },
    )
    return () => { cancelled = true }
  }, [memesApi, reloadKey])

  useEffect(() => {
    if (openId === null) return
    let cancelled = false
    memesApi.getRecutProposal(openId).then(
      (data) => { if (!cancelled) setLoaded({ id: openId, state: { status: "ready", data } }) },
      (error) => {
        if (cancelled) return
        // 404: no such proposal; 422: the id is not even a valid id (a mangled ?cut=). Either way there is nothing to retry.
        const missing = error instanceof CuratorRequestError && (error.status === 404 || error.status === 422)
        setLoaded({ id: openId, state: { status: missing ? "missing" : "failed" } })
      },
    )
    return () => { cancelled = true }
  }, [memesApi, openId, reloadKey])

  // Identity changes only when the loaded list does, so a failed request does not retrigger an effect that calls it.
  const loadMore = useCallback(() => {
    if (list.status !== "ready" || loadingMoreRef.current || list.items.length >= list.total) return
    loadingMoreRef.current = true
    setLoadingMore(true)
    memesApi.listRecutProposals(list.items.length, PAGE_SIZE).then(
      (page) => setList((previous) => ({
        status: "ready",
        total: page.total,
        items: [...previous.items, ...page.items.filter((item) => !previous.items.some((known) => known.id === item.id))],
      })),
      () => {},
    ).finally(() => {
      loadingMoreRef.current = false
      setLoadingMore(false)
    })
  }, [memesApi, list.status, list.items.length, list.total])

  function reload() {
    setList(LOADING)
    setLoaded(null)
    setReloadKey((key) => key + 1)
  }

  return {
    items: list.items,
    total: list.total,
    listStatus: list.status,
    loadingMore,
    loadMore,
    detail: openId !== null && loaded !== null && loaded.id === openId ? loaded.state : null,
    reload,
  }
}
