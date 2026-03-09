import { useCallback, useEffect, useRef, useState } from "react"
import { Meme } from "../types/meme"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { MemesApi } from "../api/MemesApi"

type MemesListProps = {
  memesApi: MemesApi
  baseUrl: string
  filter?: string
}

export function MemesList({ memesApi, baseUrl, filter }: MemesListProps) {
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
  }, [filter])

  const loadMemes = useCallback(async (next: string | undefined) => {
      if (loading) return
      setLoading(true)

      const response = await memesApi.searchMemes({
        cursor: next,
        limit: 12, 
        query: filter
      })

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

      // console.log("response: " + JSON.stringify(response))
      // console.log("cursor: " + response.nextCursor)
      // console.log("Has next: " + response.hasNext)
      setCursor(response.nextCursor)
      setLoading(false)
      setHasMore(response.hasNext!)
    },
    [loading, hasMore, filter]
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
            baseUrl={baseUrl}
            onClick={() => setSelectedMeme(meme)}
          />
        ))}
      </div>

        {
          // todo: reset cursor when query changes
        }
      {selectedMeme && (
        <MemeDetailsModal
          meme={selectedMeme}
          baseUrl={baseUrl}
          onClose={() => setSelectedMeme(null)}
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
