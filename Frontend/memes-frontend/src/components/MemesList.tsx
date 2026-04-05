import { useCallback, useEffect, useRef, useState } from "react"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { MemesApi } from "../api/MemesApi"
import { Facet } from "../types/facet"
import { Meme } from "../types/generated/all"

type MemesListProps = {
  memesApi: MemesApi
  filter?: string
  onFacetsChanged?: (facets: Facet[]) => void
  tagFilters?: Record<string, string[]>
}

export function MemesList({ memesApi, filter, onFacetsChanged, tagFilters }: MemesListProps) {
  const [memes, setMemes] = useState<Meme[]>([])
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)

  const observerRef = useRef<IntersectionObserver | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)
  const emptyRef = useRef<HTMLDivElement | null>(null) // fix 3: separate ref

  // fix 2: use refs so loadMemes stays stable
  const loadingRef = useRef(false)
  const hasMoreRef = useRef(true)
  const cursorRef = useRef<string | undefined>(undefined)

  useEffect(() => {
    loadMemes(undefined)
    setCursor(undefined)
    cursorRef.current = undefined
    window.scrollTo({ top: 0 })
  }, [filter, tagFilters])

  const loadMemes = useCallback(async (next: string | undefined) => {
    if (loadingRef.current) return
    loadingRef.current = true
    setLoading(true)

    if (filter && filter.length > 0 && filter.length < 2) {
      setMemes([])
      loadingRef.current = false
      setLoading(false)
      return
    }

    const tags = tagFilters
      ? Object.entries(tagFilters).flatMap(([name, values]) =>
          values.map(value => ({ category: name, name: value }))
        )
      : []

    const response = await memesApi.searchMemes({
      cursor: next,
      limit: 20,
      query: filter,
      tags,
    })

    if (onFacetsChanged) onFacetsChanged(response.facets!)

    setMemes(prev =>
      next
        ? [...prev, ...(response.items || []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] }))]
        : (response.items || []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] }))
    )

    const nextCursor = response.nextCursor
    cursorRef.current = nextCursor
    setCursor(nextCursor)

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
            loadMemes(nextCursor)
          }
        }, 0)
      }
    }
  }, [filter, tagFilters, memesApi, onFacetsChanged]) // fix 2: no loading/hasMore

  useEffect(() => {
    if (!sentinelRef.current) return

    observerRef.current = new IntersectionObserver(
      entries => {
        if (entries[0].isIntersecting && hasMoreRef.current && !loadingRef.current) {
          loadMemes(cursorRef.current) // fix 2: use ref for cursor too
        }
      },
      { root: null, rootMargin: "200px", threshold: 0 }
    )

    observerRef.current.observe(sentinelRef.current)

    return () => observerRef.current?.disconnect()
  }, [loadMemes]) // fix 1: loadMemes in deps

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
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


