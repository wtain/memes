import { useCallback, useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import { CuratorRequestError } from "../api/errors"
import type { Box } from "./boxOverlay"
import type { RecutProposal, RecutProposalDetail } from "./types"

export const PAGE_SIZE = 50

type ListState = { items: RecutProposal[]; total: number; status: "loading" | "ready" | "failed" }
export type DetailState =
  | { status: "ready"; data: RecutProposalDetail }
  | { status: "missing" }
  | { status: "failed" }
type Loaded = { id: string; state: DetailState }

export type DecisionState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "failed" }
  // `redirect`: the page still has to move off the refused proposal once the reloaded queue is in (one-shot).
  | { status: "conflict"; code: string; redirect: boolean }
export type DecideResult = { outcome: "ignored" | "failed" | "conflict" } | { outcome: "done"; next: string | null }
export type AdjustResult = { outcome: "done" | "ignored" | "invalid" | "failed" | "conflict" }

const LOADING: ListState = { items: [], total: 0, status: "loading" }

export function useRecutQueue(memesApi: MemesApi, openId: string | null) {
  const [list, setList] = useState<ListState>(LOADING)
  const [loadingMore, setLoadingMore] = useState(false)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [decision, setDecision] = useState<DecisionState>({ status: "idle" })
  const [adjusting, setAdjusting] = useState(false)

  // Only touched in handlers and effects: one next-page request at a time, one decision at a time.
  const loadingMoreRef = useRef(false)
  const decidingRef = useRef(false)

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

  const reload = useCallback(() => {
    setList(LOADING)
    setLoaded(null)
    setReloadKey((key) => key + 1)
  }, [])

  const decide = useCallback(async (kind: "approve" | "reject"): Promise<DecideResult> => {
    const current = loaded !== null && loaded.id === openId && loaded.state.status === "ready" ? loaded.state.data : null
    if (current === null || current.state !== "proposed" || decidingRef.current) return { outcome: "ignored" }
    decidingRef.current = true
    setDecision({ status: "saving" })
    try {
      if (kind === "approve") await memesApi.approveRecutProposal(current.id)
      else await memesApi.rejectRecutProposal(current.id)
      setList((previous) => ({
        ...previous,
        items: previous.items.filter((item) => item.id !== current.id),
        total: Math.max(0, previous.total - 1),
      }))
      setLoaded(null)
      setDecision({ status: "idle" })
      return { outcome: "done", next: current.nextId ?? current.previousId }
    } catch (error) {
      if (error instanceof CuratorRequestError && error.status === 409) {
        setDecision({ status: "conflict", code: error.code ?? "", redirect: true })
        reload()
        return { outcome: "conflict" }
      }
      setDecision({ status: "failed" })
      return { outcome: "failed" }
    } finally {
      decidingRef.current = false
    }
  }, [memesApi, loaded, openId, reload])

  // Shares decidingRef with decide(): an adjustment and a decision never overlap. A refusal (409) goes through the same
  // conflict flow as a refused decision; a 422 or any other failure is the caller's to show (the draft stays editable).
  const adjust = useCallback(async (box: Box): Promise<AdjustResult> => {
    const current = loaded !== null && loaded.id === openId && loaded.state.status === "ready" ? loaded.state.data : null
    if (current === null || current.state !== "proposed" || decidingRef.current) return { outcome: "ignored" }
    decidingRef.current = true
    setAdjusting(true)
    try {
      const updated = await memesApi.adjustRecutProposal(current.id, box)
      setLoaded({ id: current.id, state: { status: "ready", data: updated } })
      setList((previous) => ({
        ...previous,
        items: previous.items.map((item) => item.id === updated.id
          ? { ...item, box: updated.box, cutImageId: updated.cutImageId } : item),
      }))
      return { outcome: "done" }
    } catch (error) {
      if (error instanceof CuratorRequestError && error.status === 409) {
        setDecision({ status: "conflict", code: error.code ?? "", redirect: true })
        reload()
        return { outcome: "conflict" }
      }
      if (error instanceof CuratorRequestError && error.status === 422) return { outcome: "invalid" }
      return { outcome: "failed" }
    } finally {
      decidingRef.current = false
      setAdjusting(false)
    }
  }, [memesApi, loaded, openId, reload])

  const settleConflict = useCallback(() => {
    setDecision((previous) => (previous.status === "conflict" ? { ...previous, redirect: false } : previous))
  }, [])

  return {
    items: list.items,
    total: list.total,
    listStatus: list.status,
    loadingMore,
    loadMore,
    detail: openId !== null && loaded !== null && loaded.id === openId ? loaded.state : null,
    reload,
    decision,
    decide,
    adjust,
    adjusting,
    settleConflict,
  }
}
