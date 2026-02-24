import { useCallback, useEffect, useRef, useState } from "react"
import { Meme } from "../types/meme"
import MemeCard from "../components/MemeCard"
import { MockMemesApi } from "../api/mock/MockMemesApi"
import { HttpMemesApi } from "../api/http/HttpMemesApi"
import { MemesApi } from "../api/MemesApi"

type ExplorePageProps = {
  baseUrl: string
}

export default function ExplorePage({ baseUrl }: ExplorePageProps) {
  const [memes, setMemes] = useState<Meme[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [hasMore, setHasMore] = useState(true)

  const observerRef = useRef<IntersectionObserver | null>(null)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  // useEffect(() => {
  //   fetchMemes().then(r => {
  //     console.log(r)
  //     setMemes(r)
  //   })
  // }, [])

  // const USE_MOCK = import.meta.env.DEV
  const USE_MOCK = false

  useEffect(() => {
    loadMemes(null)
  }, [])

  const loadMemes = useCallback(async (next: string | null) => {
    if (loading) return
    setLoading(true)

    const memesApi: MemesApi = USE_MOCK ? new MockMemesApi() : new HttpMemesApi(baseUrl) // import.meta.env.VITE_API_BASE_URL

    const response = await memesApi.searchMemes({
      cursor: next,
      limit: 12,
    })

    setMemes(prev =>
      next ? [...prev, ...response.items] : response.items
    )

    console.log("response: " + JSON.stringify(response))
    console.log("cursor: " + response.nextCursor)
    setCursor(response.nextCursor!)
    setLoading(false)
  },
    [loading, hasMore]
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

  // async function fetchMemes(next: string | null): Promise<Meme[]> {

  //   if (loading) return
  //   setLoading(true)

  //   const memesApi: MemesApi = USE_MOCK ? new MockMemesApi() : new HttpMemesApi("http://127.0.0.1:8081/") // import.meta.env.VITE_API_BASE_URL

  //   return memesApi.searchMemes({
  //     cursor: next,
  //     limit: 12,
  //   }).then(r => r.items!);

  //   // await new Promise((r) => setTimeout(r, 300))
  //   // return MOCK_MEMES
  // }

  // todo: api url copy-pasted
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Explore</h1>

      {/* <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {memes.map((meme) => (
          <table>
            <tr>
              <td>
                <img key={meme.id}
                     src={"http://127.0.0.1:8081" + meme.imageUrl}
                     className="rounded-lg"
                            />
              </td>
            </tr>
            {meme.text.map(line => (
              <tr><td>{line}</td></tr>
            ))}
          </table>
        ))}
      </div> */}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        {memes.map(meme => (
          <MemeCard
            key={meme.id}
            meme={meme}
            baseUrl={baseUrl}
          />
        ))}
      </div>

      {/* {cursor && (
        <div className="mt-6 text-center">
          <button
            onClick={() => loadMemes(cursor)}
            className="px-4 py-2 bg-black text-white rounded-lg"
          >
            {loading ? "Loading..." : "Load more"}
          </button>
        </div>
      )} */}

      
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
