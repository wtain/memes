import { useCallback, useEffect, useReducer, useRef, useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { initialSearchState, searchReducer } from "../../comics/editorReducer"
import type { AddabilityItem } from "../../comics/editorReducer"

export type AddOutcome = "added" | "conflict" | "error"

type Props = {
  memesApi: MemesApi
  comicsId: string
  /** Changes whenever the open comics' panels change; the visible results are then re-checked. */
  panelSignature: string | null
  onAdd: (imageId: string) => Promise<AddOutcome>
}

const PAGE_SIZE = 24

const REASON_TEXT: Record<string, string> = {
  in_comics: "In another comics",
  flagged: "Flagged",
  live_cut: "Has a recut cut",
  not_active: "Not active",
  not_found: "Not found",
}

export default function ComicsSearchColumn({ memesApi, comicsId, panelSignature, onAdd }: Props) {
  const [state, dispatch] = useReducer(searchReducer, initialSearchState)
  const [input, setInput] = useState("")
  const [query, setQuery] = useState("")
  const [loadingMore, setLoadingMore] = useState(false)
  const [adding, setAdding] = useState<string | null>(null)

  const itemsRef = useRef(state.items)
  useEffect(() => { itemsRef.current = state.items }, [state.items])
  // Per image, the newest addability request; an older response for the same image is dropped.
  const requestSeq = useRef(0)
  const latestFor = useRef(new Map<string, number>())

  const checkAddability = useCallback((ids: string[]) => {
    if (ids.length === 0) return
    const seq = ++requestSeq.current
    for (const id of ids) latestFor.current.set(id, seq)
    memesApi.comicsAddability(ids).then(
      (result) => {
        const items = (result.items ?? []).filter((item) => latestFor.current.get(item.imageId) === seq)
        if (items.length > 0) dispatch({ type: "addability", items })
      },
      () => { /* addability stays unknown: "+" stays disabled */ },
    )
  }, [memesApi])

  useEffect(() => {
    if (query === "") return
    let cancelled = false
    memesApi.searchMemes({ query, limit: PAGE_SIZE, record: false }).then(
      (page) => {
        if (cancelled) return
        const items = page.items ?? []
        dispatch({ type: "loaded", key: query, items, nextCursor: page.nextCursor ?? null, hasNext: page.hasNext === true })
        checkAddability(items.map((item) => item.id))
      },
      () => { if (!cancelled) dispatch({ type: "failed", key: query }) },
    )
    return () => { cancelled = true }
  }, [memesApi, query, checkAddability])

  const lastSignature = useRef(panelSignature)
  useEffect(() => {
    if (lastSignature.current === panelSignature) return
    lastSignature.current = panelSignature
    checkAddability(itemsRef.current.map((item) => item.id))
  }, [panelSignature, checkAddability])

  const current = state.key === query && query !== ""

  async function showMore() {
    if (!current || state.nextCursor === null || loadingMore) return
    const key = query
    setLoadingMore(true)
    try {
      const page = await memesApi.searchMemes({ query: key, limit: PAGE_SIZE, cursor: state.nextCursor, record: false })
      const items = page.items ?? []
      dispatch({ type: "appended", key, items, nextCursor: page.nextCursor ?? null, hasNext: page.hasNext === true })
      checkAddability(items.map((item) => item.id))
    } catch {
      dispatch({ type: "failed", key })
    } finally {
      setLoadingMore(false)
    }
  }

  async function add(imageId: string) {
    setAdding(imageId)
    try {
      const outcome = await onAdd(imageId)
      if (outcome === "conflict") checkAddability(state.items.map((item) => item.id))
    } finally {
      setAdding(null)
    }
  }

  function renderState(imageId: string, info: AddabilityItem | undefined, label: string) {
    if (info === undefined) {
      return <button type="button" disabled aria-label={`Add ${label}`} className="min-h-11 min-w-11 rounded border px-2 opacity-50">+</button>
    }
    if (info.addable) {
      return (
        <button type="button" aria-label={`Add ${label}`} disabled={adding !== null}
                onClick={() => void add(imageId)} className="min-h-11 min-w-11 rounded border px-2 disabled:opacity-50">+</button>
      )
    }
    if (info.reason === "in_comics" && info.comicsId === comicsId) {
      return <span className="text-sm">added</span>
    }
    return (
      <span className="flex flex-col items-start gap-1 text-sm">
        <button type="button" disabled aria-label={`Add ${label}`} className="min-h-11 min-w-11 rounded border px-2 opacity-50">+</button>
        {info.reason === "in_comics" && info.comicsId !== null ? (
          <span>In <Link to={`/admin/comics/${info.comicsId}`} className="underline">{info.comicsName ?? "another comics"}</Link></span>
        ) : (
          <span>{REASON_TEXT[info.reason ?? ""] ?? "Can't be added"}</span>
        )}
      </span>
    )
  }

  return (
    <section aria-label="Find memes">
      <form className="mb-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); setQuery(input.trim()) }}>
        <input type="search" aria-label="Search memes" value={input} onChange={(event) => setInput(event.target.value)}
               className="min-h-11 min-w-0 flex-1 rounded border px-3" />
        <button type="submit" className="min-h-11 rounded border px-3">Search</button>
      </form>
      {query === "" && <p>Search for memes to add as panels</p>}
      {query !== "" && !current && !state.failed && <p role="status">Searching...</p>}
      {state.failed && <p role="alert">Could not load results</p>}
      {current && state.items.length === 0 && !state.failed && <p>No memes found</p>}
      {current && state.items.length > 0 && (
        <ul className="grid gap-2">
          {state.items.map((meme) => {
            const label = meme.originalFileName ?? meme.id
            return (
              <li key={meme.id} className="flex items-center gap-2 rounded border p-2">
                <img src={meme.imageUrl} alt={label} className="h-16 w-16 shrink-0 rounded object-cover" />
                <span className="min-w-0 flex-1 truncate text-sm">{label}</span>
                {renderState(meme.id, state.addability[meme.id], label)}
              </li>
            )
          })}
        </ul>
      )}
      {current && state.hasNext && (
        <button type="button" disabled={loadingMore} onClick={() => void showMore()}
                className="mt-3 min-h-11 rounded border px-3 disabled:opacity-50">Show more</button>
      )}
    </section>
  )
}
