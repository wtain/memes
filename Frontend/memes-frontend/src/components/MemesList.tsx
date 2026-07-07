import { useCallback, useEffect, useRef, useState } from "react"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import type { MemesApi } from "../api/MemesApi"
import type { Facet, Meme } from "../types/generated/all"

type MemesListProps = {
  memesApi: MemesApi
  filter?: string
  onFacetsChanged?: (facets: Facet[]) => void
  tagFilters?: Record<string, string[]>
  listUntagged?: boolean
  listDuplicates?: boolean
  listFlagged?: boolean
  listNoOcr?: boolean
  listRecommendations?: boolean
}

export function MemesList({ memesApi, filter, onFacetsChanged, tagFilters, listUntagged, listDuplicates, listFlagged, listNoOcr, listRecommendations }: MemesListProps) {
  const [memes, setMemes] = useState<Meme[]>([])
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)

  const observerRef = useRef<IntersectionObserver | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const emptyRef = useRef<HTMLDivElement | null>(null)

  const loadingRef = useRef(false)
  const hasMoreRef = useRef(true)
  const cursorRef = useRef<string | undefined>(undefined)
  const loadMemesRef = useRef<(next: string | undefined) => void>(() => {})

  const loadMemes = useCallback(async (next: string | undefined) => {
    if (loadingRef.current) return
    loadingRef.current = true
    // Yield before any setState so callers inside useEffect don't trigger synchronous cascading renders
    await Promise.resolve()

    if (filter && filter.length > 0 && filter.length < 2) {
      setMemes([])
      loadingRef.current = false
      setLoading(false)
      return
    }

    setLoading(true)

    const tags = tagFilters
      ? Object.entries(tagFilters).flatMap(([name, values]) =>
          values.map(value => ({ category: name, name: value }))
        )
      : []

    const response = await getResponseFromBackend()

    if (onFacetsChanged) onFacetsChanged(response.facets!)

    setMemes(prev =>
      next
        ? [...prev, ...(response.items || []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] }))]
        : (response.items || []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] }))
    )

    const nextCursor = response.nextCursor
    cursorRef.current = nextCursor

    loadingRef.current = false
    setLoading(false)

    hasMoreRef.current = response.hasNext!
    setHasMore(response.hasNext!)

    // 👇 After load completes, check if sentinel is still visible and keep paginating
    if (response.hasNext && sentinelRef.current) {
      const rect = sentinelRef.current.getBoundingClientRect()
      if (rect.top < window.innerHeight + 200) {
        // Use setTimeout to yield to React's state updates first
        setTimeout(() => {
          if (hasMoreRef.current && !loadingRef.current) {
            loadMemesRef.current(nextCursor)
          }
        }, 0)
      }
    }

    async function getResponseFromBackend() {
      if (listUntagged !== undefined && listUntagged) {
        return await memesApi.iterateUntaggedMemes(
          21,
          next,
        )  
      }
      if (listDuplicates !== undefined && listDuplicates) {
        return await memesApi.iterateDuplicates(40, next, 0.2)
      }
      if (listFlagged) {
        return await memesApi.iterateFlaggedMemes(40, next)
      }
      if (listNoOcr) {
        return await memesApi.iterateNoOcrMemes(21, next)
      }
      if (listRecommendations) {
        return await memesApi.getRecommendations(filter, 36, next)
      }
      return await memesApi.searchMemes({
        cursor: next,
        limit: 36,
        query: filter,
        tags,
      })
    }
  }, [filter, tagFilters, memesApi, onFacetsChanged, listUntagged, listDuplicates, listFlagged, listNoOcr, listRecommendations])

  useEffect(() => { loadMemesRef.current = loadMemes })

  useEffect(() => {
    loadMemesRef.current(undefined)
    cursorRef.current = undefined
    window.scrollTo({ top: 0 })
  }, [filter, tagFilters])

  useEffect(() => {
    if (!sentinelRef.current) return

    observerRef.current = new IntersectionObserver(
      entries => {
        if (entries[0].isIntersecting && hasMoreRef.current && !loadingRef.current) {
          loadMemes(cursorRef.current)
        }
      },
      { root: null, rootMargin: "200px", threshold: 0 }
    )

    observerRef.current.observe(sentinelRef.current)

    return () => observerRef.current?.disconnect()
  }, [loadMemes])

  return (
    <div>
      <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
        {memes.map(meme => (
          <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
        ))}
      </div>

      {selectedMeme && (
        <MemeDetailsModal meme={selectedMeme} onClose={() => setSelectedMeme(null)} memesApi={memesApi} />
      )}

      {hasMore && (
        <div ref={sentinelRef} className="h-10 flex items-center justify-center">
          {loading && <span>Loading...</span>}
        </div>
      )}

      {memes.length === 0 && !loading && (
        <div ref={emptyRef} className="h-10 flex items-center justify-center"> {/* fix 3 */}
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}


