import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { ComicsRequestError } from "../../api/errors"
import { describeComicsError } from "../../comics/comicsErrors"
import type { ComicsDetail, ComicsSummary } from "../../types/generated/all"
import { Modal } from "../Modal"

type Props = {
  memesApi: MemesApi
  imageId: string
  onClose: () => void
  /** Called once the image is a panel of `comics`. */
  onAdded: (comics: ComicsDetail) => void
}

const PAGE_SIZE = 20
const DEBOUNCE_MS = 300

/** The drafts shown for one search request; `key` ties them to the request that produced them. */
type Loaded = { key: string; drafts: ComicsSummary[]; rawCount: number; hasMore: boolean; failed: boolean }

type Conflict = { targetId: string; fromName: string }

export function ComicsPickerModal({ memesApi, imageId, onClose, onAdded }: Props) {
  const [mode, setMode] = useState<"pick" | "new">("pick")
  const [search, setSearch] = useState("")
  const [debounced, setDebounced] = useState("")
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  const [newName, setNewName] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState<Conflict | null>(null)
  const [added, setAdded] = useState<ComicsDetail | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  const key = debounced
  useEffect(() => {
    let cancelled = false
    memesApi.listComics(debounced, 0, PAGE_SIZE).then(
      (page) => {
        if (!cancelled) setLoaded({
          key, drafts: page.items.filter((c) => c.state === "draft"), rawCount: page.items.length,
          hasMore: page.hasMore, failed: false,
        })
      },
      () => { if (!cancelled) setLoaded({ key, drafts: [], rawCount: 0, hasMore: false, failed: true }) },
    )
    return () => { cancelled = true }
  }, [memesApi, debounced, key])

  const current = loaded !== null && loaded.key === key ? loaded : null

  async function showMore() {
    if (current === null || loadingMore) return
    setLoadingMore(true)
    try {
      const page = await memesApi.listComics(debounced, current.rawCount, PAGE_SIZE)
      setLoaded((prev) => prev !== null && prev.key === key
        ? {
          ...prev, drafts: [...prev.drafts, ...page.items.filter((c) => c.state === "draft")],
          rawCount: prev.rawCount + page.items.length, hasMore: page.hasMore,
        }
        : prev)
    } catch {
      setLoaded((prev) => prev !== null && prev.key === key ? { ...prev, hasMore: false, failed: true } : prev)
    } finally {
      setLoadingMore(false)
    }
  }

  async function addTo(targetId: string, move: boolean) {
    if (busy) return
    setBusy(true)
    setError(null)
    setConflict(null)
    try {
      const detail = move
        ? await memesApi.addComicsPanel(targetId, imageId, true)
        : await memesApi.addComicsPanel(targetId, imageId)
      setAdded(detail)
      onAdded(detail)
    } catch (e) {
      setError(describeComicsError(e))
      if (e instanceof ComicsRequestError && e.code === "image_in_comics" && typeof e.detail.comicsName === "string") {
        setConflict({ targetId, fromName: e.detail.comicsName })
      }
    } finally {
      setBusy(false)
    }
  }

  async function createAndAdd() {
    if (busy) return
    setBusy(true)
    setError(null)
    setConflict(null)
    try {
      const created = await memesApi.createComics(newName.trim())
      const detail = await memesApi.addComicsPanel(created.id, imageId)
      setAdded(detail)
      onAdded(detail)
    } catch (e) {
      setError(describeComicsError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Add to comics" onClose={onClose}>
      {added !== null ? (
        <div className="space-y-3">
          <p role="status">Added to {added.name}.</p>
          <div className="flex gap-3">
            <Link to={`/admin/comics/${added.id}`} className="text-blue-600 hover:underline">Open the editor</Link>
            <button type="button" onClick={onClose} className="px-3 py-1 border rounded cursor-pointer">Close</button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2">
            <button type="button" aria-pressed={mode === "pick"} onClick={() => setMode("pick")}
              className={`px-3 py-1 border rounded cursor-pointer ${mode === "pick" ? "bg-gray-200" : ""}`}>
              Existing comics
            </button>
            <button type="button" aria-pressed={mode === "new"} onClick={() => setMode("new")}
              className={`px-3 py-1 border rounded cursor-pointer ${mode === "new" ? "bg-gray-200" : ""}`}>
              New comics
            </button>
          </div>

          {mode === "pick" ? (
            <div className="space-y-2">
              <input type="search" value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder="Search by name" aria-label="Search comics" className="border rounded px-2 py-1 w-full" />
              {current === null && <p className="text-gray-500">Loading…</p>}
              {current?.failed && <p role="alert" className="text-red-600">Could not load the comics</p>}
              {current !== null && !current.failed && current.drafts.length === 0 && (
                <p className="text-gray-500">No draft comics found</p>
              )}
              <ul className="space-y-1">
                {current?.drafts.map((c) => (
                  <li key={c.id}>
                    <button type="button" disabled={busy} onClick={() => addTo(c.id, false)}
                      className="w-full text-left px-2 py-1 border rounded cursor-pointer hover:bg-gray-50 disabled:opacity-50">
                      {c.name} <span className="text-gray-500">({c.panelCount} {c.panelCount === 1 ? "panel" : "panels"})</span>
                    </button>
                  </li>
                ))}
              </ul>
              {current?.hasMore && (
                <button type="button" disabled={loadingMore} onClick={showMore}
                  className="px-3 py-1 border rounded cursor-pointer disabled:opacity-50">Show more</button>
              )}
            </div>
          ) : (
            <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); void createAndAdd() }}>
              <label className="block">
                <span className="block text-sm">Name</span>
                <input type="text" value={newName} onChange={(e) => setNewName(e.target.value)}
                  className="border rounded px-2 py-1 w-full" />
              </label>
              <button type="submit" disabled={busy || newName.trim() === ""}
                className="px-3 py-1 border rounded cursor-pointer disabled:opacity-50">Create and add</button>
            </form>
          )}

          {error !== null && <p role="alert" className="text-red-600">{error}</p>}
          {conflict !== null && (
            <button type="button" disabled={busy} onClick={() => addTo(conflict.targetId, true)}
              className="px-3 py-1 border rounded cursor-pointer disabled:opacity-50">
              Move from {conflict.fromName}
            </button>
          )}
        </div>
      )}
    </Modal>
  )
}
