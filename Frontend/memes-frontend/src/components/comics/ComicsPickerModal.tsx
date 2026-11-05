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
/** Pages without a draft that are skipped in a row before the user is asked to continue. */
const MAX_EMPTY_PAGES = 10

/** The drafts shown for one search request; `key` ties them to the request that produced them. */
type Loaded = { key: string; drafts: ComicsSummary[]; rawCount: number; hasMore: boolean; failed: boolean }

type Conflict = { targetId: string; fromName: string }

/**
 * Reads pages from `offset` until one has a draft, the list ends, or MAX_EMPTY_PAGES pages in a row
 * had none. `count` is how many rows were read (the next offset is `offset + count`).
 * Returns null when `isCancelled` turned true while reading.
 */
async function readDrafts(
  memesApi: MemesApi, q: string, offset: number, isCancelled: () => boolean,
): Promise<{ drafts: ComicsSummary[]; count: number; hasMore: boolean } | null> {
  let count = 0
  for (let empty = 1; ; empty++) {
    const page = await memesApi.listComics(q, offset + count, PAGE_SIZE)
    if (isCancelled()) return null
    count += page.items.length
    const drafts = page.items.filter((c) => c.state === "draft")
    if (drafts.length > 0 || !page.hasMore || empty >= MAX_EMPTY_PAGES) {
      return { drafts, count, hasMore: page.hasMore }
    }
  }
}

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
  // The comics a "Create and add" made; a retry after a failed add reuses it instead of creating another.
  const [createdId, setCreatedId] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  const key = debounced
  useEffect(() => {
    let cancelled = false
    readDrafts(memesApi, debounced, 0, () => cancelled).then(
      (result) => {
        if (result !== null) setLoaded({
          key, drafts: result.drafts, rawCount: result.count, hasMore: result.hasMore, failed: false,
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
      const result = await readDrafts(memesApi, debounced, current.rawCount, () => false)
      setLoaded((prev) => prev !== null && prev.key === key && result !== null
        ? {
          ...prev, drafts: [...prev.drafts, ...result.drafts],
          rawCount: prev.rawCount + result.count, hasMore: result.hasMore,
        }
        : prev)
    } catch {
      setLoaded((prev) => prev !== null && prev.key === key ? { ...prev, hasMore: false, failed: true } : prev)
    } finally {
      setLoadingMore(false)
    }
  }

  function switchMode(next: "pick" | "new") {
    setMode(next)
    setError(null)
    setConflict(null)
  }

  async function addTo(targetId: string, move: boolean) {
    if (busy) return
    const fromName = conflict?.fromName
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
      // A move that fails with `built` means the comics the image is in (the source) is built.
      setError(move && e instanceof ComicsRequestError && e.code === "built" && fromName !== undefined
        ? `The comics "${fromName}" is built, its panels can't be moved`
        : describeComicsError(e))
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
    let targetId = createdId
    try {
      if (targetId === null) {
        targetId = (await memesApi.createComics(newName.trim())).id
        setCreatedId(targetId)
      }
      const detail = await memesApi.addComicsPanel(targetId, imageId)
      setAdded(detail)
      onAdded(detail)
    } catch (e) {
      setError(describeComicsError(e))
      if (e instanceof ComicsRequestError && e.code === "image_in_comics" && typeof e.detail.comicsName === "string" && targetId !== null) {
        setConflict({ targetId, fromName: e.detail.comicsName })
      }
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
            <button type="button" aria-pressed={mode === "pick"} onClick={() => switchMode("pick")}
              className={`px-3 py-1 border rounded cursor-pointer ${mode === "pick" ? "bg-gray-200" : ""}`}>
              Existing comics
            </button>
            <button type="button" aria-pressed={mode === "new"} onClick={() => switchMode("new")}
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
