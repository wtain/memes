import { useCallback, useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"
import MemeCard from "../components/MemeCard"
import { MemeDetailsModal } from "../components/MemeDetailsModal"
import type { MemesApi } from "../api/MemesApi"
import type { Meme, TrendingMemeItem } from "../types/generated/all"

const PAGE_SIZE = 24

type TrendingWindow = "all" | "30d"
type List = { window: TrendingWindow; items: TrendingMemeItem[] }

type Props = { memesApi: MemesApi }

const OPTIONS: { value: TrendingWindow; label: string }[] = [
  { value: "all", label: "All time" },
  { value: "30d", label: "Last 30 days" },
]

export default function TrendingMemesPage({ memesApi }: Props) {
  const [searchParams, setSearchParams] = useSearchParams()
  const timeWindow: TrendingWindow = searchParams.get("window") === "30d" ? "30d" : "all"

  const [list, setList] = useState<List>({ window: timeWindow, items: [] })
  const [nextCursor, setNextCursor] = useState<string | undefined>(undefined)
  const [hasNext, setHasNext] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)
  const [selected, setSelected] = useState<Meme | null>(null)
  const requestId = useRef(0)

  // Cards of another window are never shown, even for the render before its load starts.
  const stale = list.window !== timeWindow
  const items = stale ? [] : list.items

  const load = useCallback((target: TrendingWindow, cursor?: string) => {
    const id = ++requestId.current
    setLoading(true)
    setError(false)
    if (cursor === undefined) {
      setList({ window: target, items: [] })
      setNextCursor(undefined)
      setHasNext(false)
    }
    memesApi.getTrendingMemes(PAGE_SIZE, cursor, target)
      .then(response => {
        if (id !== requestId.current) return
        setList(prev => {
          const seen = new Set(prev.items.map(entry => entry.meme.id))
          return {
            window: target,
            items: [...prev.items, ...response.items.filter(entry => !seen.has(entry.meme.id))],
          }
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

  // StrictMode mounts effects twice; remembering the window whose first page was requested
  // keeps each window to a single first request, while a window change loads again.
  const startedWindow = useRef<TrendingWindow | null>(null)
  useEffect(() => {
    if (startedWindow.current === timeWindow) return
    startedWindow.current = timeWindow
    load(timeWindow)
  }, [load, timeWindow])

  const choose = (value: TrendingWindow) => {
    if (value === timeWindow) return
    setSearchParams(value === "30d" ? { window: "30d" } : {})
  }

  const isLoading = loading || stale

  return (
    <div className="p-4">
      <h1 className="text-xl font-semibold mb-4">Trending memes</h1>

      <div className="mb-4 flex gap-2" role="group" aria-label="Time window">
        {OPTIONS.map(option => (
          <button
            key={option.value}
            aria-pressed={timeWindow === option.value}
            className={`rounded border px-4 py-2 min-h-11 ${
              timeWindow === option.value
                ? "border-amber-600 bg-amber-100 text-amber-900"
                : "border-gray-300 hover:bg-gray-50"
            }`}
            onClick={() => choose(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>

      {error && !stale && (
        <div role="alert" className="mb-4 text-red-600">
          Could not load trending memes.
          <button
            className="ml-3 underline"
            onClick={() => load(timeWindow, items.length > 0 ? nextCursor : undefined)}
          >
            Retry
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        {items.map(({ meme, popularity, windowGain }) => (
          <div key={meme.id} className="flex flex-col gap-1">
            <MemeCard meme={meme} memesApi={memesApi} onClick={() => setSelected(meme)} />
            <div className="flex flex-wrap gap-1">
              {timeWindow === "30d" && windowGain !== undefined && (
                <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                  +{windowGain} in 30 days
                </span>
              )}
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                Popularity {popularity}
              </span>
            </div>
          </div>
        ))}
      </div>

      {isLoading && <div role="status" className="h-10 flex items-center justify-center">Loading...</div>}

      {!isLoading && !error && items.length === 0 && (
        <div className="h-10 flex items-center justify-center">
          {timeWindow === "30d"
            ? "No memes picked up duplicates in the last 30 days."
            : "No trending memes yet."}
        </div>
      )}

      {!isLoading && hasNext && (
        <div className="mt-4 flex justify-center">
          <button
            className="rounded border border-gray-300 px-4 py-2 min-h-11 hover:bg-gray-50"
            onClick={() => load(timeWindow, nextCursor)}
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
