import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { Category, ProfileCoverage, ProfileCoverageImage, ProfileCoverageImages } from "./types"

const FIRST_CATEGORY: Category = "without_profile"

type Page = { category: Category; items: ProfileCoverageImage[]; next: string | null; total: number }
type Shared<T> = { api: MemesApi; promise: Promise<T> }

export function useProfileCoverage(memesApi: MemesApi) {
  const [coverageState, setCoverageState] = useState<{ data: ProfileCoverage | null; failed: boolean } | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [category, setCategory] = useState<Category>(FIRST_CATEGORY)
  const [page, setPage] = useState<Page | null>(null)
  const [loadingFor, setLoadingFor] = useState<Category | null>(FIRST_CATEGORY)
  const [failedFor, setFailedFor] = useState<Category | null>(null)

  // Only touched in handlers and effects: the newest list request wins, and a double mount reuses
  // the request the first mount started instead of repeating it.
  const requestSeq = useRef(0)
  const selected = useRef<Category>(FIRST_CATEGORY)
  const sharedCoverage = useRef<Shared<ProfileCoverage> | null>(null)
  const sharedFirstPage = useRef<Shared<ProfileCoverageImages> | null>(null)

  const current = page !== null && page.category === category ? page : null
  const items = current?.items ?? []
  const total = current?.total ?? 0
  const next = current?.next ?? null

  useEffect(() => {
    let cancelled = false
    const shared = sharedCoverage.current
    const promise = shared !== null && shared.api === memesApi && reloadKey === 0
      ? shared.promise
      : memesApi.getProfileCoverage()
    sharedCoverage.current = { api: memesApi, promise }
    promise.then(
      (data) => { if (!cancelled) setCoverageState({ data, failed: false }) },
      () => { if (!cancelled) setCoverageState({ data: null, failed: true }) },
    )
    return () => { cancelled = true }
  }, [memesApi, reloadKey])

  useEffect(() => {
    const seq = ++requestSeq.current
    let cancelled = false
    const shared = sharedFirstPage.current
    const promise = shared !== null && shared.api === memesApi
      ? shared.promise
      : memesApi.getProfileCoverageImages(FIRST_CATEGORY)
    sharedFirstPage.current = { api: memesApi, promise }
    promise.then(
      (result) => {
        if (cancelled || seq !== requestSeq.current) return
        setPage({ category: FIRST_CATEGORY, items: result.items, next: result.nextCursor, total: result.total })
        setLoadingFor(null)
      },
      () => {
        if (cancelled || seq !== requestSeq.current) return
        setFailedFor(FIRST_CATEGORY)
        setLoadingFor(null)
      },
    )
    return () => { cancelled = true }
  }, [memesApi])

  function fetchPage(target: Category, cursor: string | undefined, previous: ProfileCoverageImage[]) {
    const seq = ++requestSeq.current
    setLoadingFor(target)
    setFailedFor(null)
    memesApi.getProfileCoverageImages(target, cursor).then(
      (result) => {
        if (seq !== requestSeq.current || selected.current !== target) return
        setPage({ category: target, items: [...previous, ...result.items], next: result.nextCursor, total: result.total })
        setLoadingFor(null)
      },
      () => {
        if (seq !== requestSeq.current || selected.current !== target) return
        setFailedFor(target)
        setLoadingFor(null)
      },
    )
  }

  function select(target: Category) {
    if (target === category) {
      // Selecting the category again retries a failed list load (the next page when earlier items are shown).
      if (failedFor === category && loadingFor !== category) {
        if (items.length > 0 && next !== null) fetchPage(category, next, items)
        else fetchPage(category, undefined, [])
      }
      return
    }
    selected.current = target
    setCategory(target)
    fetchPage(target, undefined, [])
  }

  function loadMore() {
    if (loadingFor === category || next === null) return
    fetchPage(category, next, items)
  }

  function reload() {
    setCoverageState(null)
    setReloadKey((key) => key + 1)
  }

  return {
    coverage: coverageState?.data ?? null,
    failed: coverageState?.failed ?? false,
    category,
    select,
    items,
    total,
    loading: loadingFor === category,
    listFailed: failedFor === category,
    hasMore: next !== null,
    loadMore,
    reload,
  }
}
