import { useCallback, useEffect, useRef, useState } from "react"
import MemeCard from "../components/MemeCard"
import { MemeDetailsModal } from "../components/MemeDetailsModal"
import type { MemesApi } from "../api/MemesApi"
import type { Meme, TrendingMemeItem } from "../types/generated/all"

const PAGE_SIZE = 24

type Props = { memesApi: MemesApi }

export default function TrendingMemesPage({ memesApi }: Props) {
  const [items, setItems] = useState<TrendingMemeItem[]>([])
  const [nextCursor, setNextCursor] = useState<string | undefined>(undefined)
  const [hasNext, setHasNext] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [selected, setSelected] = useState<Meme | null>(null)
  const requestId = useRef(0)

  const load = useCallback((cursor?: string) => {
    const id = ++requestId.current
    setLoading(true)
    setError(false)
    memesApi.getTrendingMemes(PAGE_SIZE, cursor)
      .then(response => {
        if (id !== requestId.current) return
        setItems(prev => {
          const seen = new Set(prev.map(entry => entry.meme.id))
          return [...prev, ...response.items.filter(entry => !seen.has(entry.meme.id))]
        })
        setNextCursor(response.nextCursor)
        setHasNext(response.hasNext)
      })
      .catch(() => {
        if (id === requestId.current) setError(true)
      })
      .finally(() => {
        if (id === requestId.current) setLoading(false)
      })
  }, [memesApi])

  // StrictMode mounts effects twice; the guard keeps the first page to a single request.
  const firstLoadStarted = useRef(false)
  useEffect(() => {
    if (firstLoadStarted.current) return
    firstLoadStarted.current = true
    load()
  }, [load])

  return (
    <div className="p-4">
      <h1 className="text-xl font-semibold mb-4">Trending memes</h1>

      {error && (
        <div role="alert" className="mb-4 text-red-600">
          Could not load trending memes.
          <button
            className="ml-3 underline"
            onClick={() => load(items.length > 0 ? nextCursor : undefined)}
          >
            Retry
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        {items.map(({ meme, popularity }) => (
          <div key={meme.id} className="flex flex-col gap-1">
            <MemeCard meme={meme} memesApi={memesApi} onClick={() => setSelected(meme)} />
            <span className="self-start rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
              Popularity {popularity}
            </span>
          </div>
        ))}
      </div>

      {loading && <div className="h-10 flex items-center justify-center">Loading...</div>}

      {!loading && !error && items.length === 0 && (
        <div className="h-10 flex items-center justify-center">No trending memes yet.</div>
      )}

      {!loading && hasNext && (
        <div className="mt-4 flex justify-center">
          <button
            className="rounded border border-gray-300 px-4 py-2 min-h-11 hover:bg-gray-50"
            onClick={() => load(nextCursor)}
          >
            Show more
          </button>
        </div>
      )}

      {selected && (
        <MemeDetailsModal meme={selected} onClose={() => setSelected(null)} memesApi={memesApi} />
      )}
    </div>
  )
}
