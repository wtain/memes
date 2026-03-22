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

  useEffect(() => {
    loadMemes(undefined)
    setCursor(undefined)
    window.scrollTo({top: 0})
  }, [filter, tagFilters])

  const loadMemes = useCallback(async (next: string | undefined) => {
      if (loading) return
      setLoading(true)

      if (filter && filter!.length > 0 && filter!.length < 2) {
        setMemes([]);
        return;
      }

      const tags = tagFilters ? Object.entries(tagFilters!).flatMap( ([name, values]) => {
          return values.map(value => 
            {
              return {
                category: name, 
                name: value
              }
            }
          )
        }) : []

      const response = await memesApi.searchMemes({
        cursor: next,
        limit: 12, 
        query: filter,
        tags: tags
      })

      if (onFacetsChanged) {
        onFacetsChanged(response.facets!)
      }

      setMemes(prev =>
        next ? [...prev, ...(response.items || []).map(item => ({
          ...item,
          text: item.text || [],
          tags: item.tags || []
        }))] : (response.items || []).map(item => ({
          ...item,
          text: item.text || [],
          tags: item.tags || []
        }))
      )

      setCursor(response.nextCursor)
      setLoading(false)
      setHasMore(response.hasNext!)
    },
    [loading, hasMore, filter, tagFilters]
  )

  useEffect(() => {
    if (!sentinelRef.current) return

    observerRef.current = new IntersectionObserver(
      entries => {
        if (entries[0].isIntersecting && hasMore && !loading) {
          loadMemes(cursor)
        }
      },
      {
        root: null,
        rootMargin: "200px", // preload before fully visible
        threshold: 0,
      }
    )

    observerRef.current.observe(sentinelRef.current)

    return () => {
      observerRef.current?.disconnect()
    }
  }, [cursor, hasMore, loading])

  return (
    <div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {memes.map(meme => (
          <MemeCard
            key={meme.id}
            meme={meme}
            memesApi={memesApi}
            onClick={() => setSelectedMeme(meme)}
          />
        ))}
      </div>

      {selectedMeme && (
        <MemeDetailsModal
          meme={selectedMeme}
          onClose={() => setSelectedMeme(null)}
          memesApi={memesApi}
        />
      )}

      {/* Sentinel */}
      {hasMore && (
        <div
          ref={sentinelRef}
          className="h-10 flex items-center justify-center"
        >
          {loading && <span>Loading...</span>}
        </div>
      )}
    </div>
  )
}


