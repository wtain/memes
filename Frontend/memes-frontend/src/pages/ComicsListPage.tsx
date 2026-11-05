import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { describeComicsError } from "../comics/comicsErrors"
import type { ComicsSummary } from "../types/generated/all"

type Props = { memesApi: MemesApi }

const PAGE_SIZE = 24
const DEBOUNCE_MS = 300

/** What the list shows for one search request; `key` ties it to the request that produced it. */
type Loaded = { key: string; items: ComicsSummary[]; hasMore: boolean; failed: boolean }

export default function ComicsListPage({ memesApi }: Props) {
  const [search, setSearch] = useState("")
  const [debounced, setDebounced] = useState("")
  const [reloads, setReloads] = useState(0)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  const [createError, setCreateError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [confirmId, setConfirmId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [search])

  const key = `${debounced}|${reloads}`
  useEffect(() => {
    let cancelled = false
    memesApi.listComics(debounced, 0, PAGE_SIZE).then(
      (page) => { if (!cancelled) setLoaded({ key, items: page.items, hasMore: page.hasMore, failed: false }) },
      () => { if (!cancelled) setLoaded({ key, items: [], hasMore: false, failed: true }) },
    )
    return () => { cancelled = true }
  }, [memesApi, debounced, key])

  const current = loaded !== null && loaded.key === key ? loaded : null
  const items = current?.items ?? []

  async function showMore() {
    if (current === null || loadingMore) return
    setLoadingMore(true)
    try {
      const page = await memesApi.listComics(debounced, items.length, PAGE_SIZE)
      setLoaded((prev) => prev !== null && prev.key === key
        ? { ...prev, items: [...prev.items, ...page.items], hasMore: page.hasMore }
        : prev)
    } catch {
      setLoaded((prev) => prev !== null && prev.key === key ? { ...prev, hasMore: false, failed: true } : prev)
    } finally {
      setLoadingMore(false)
    }
  }

  async function create() {
    const name = newName.trim()
    setSaving(true)
    setCreateError(null)
    try {
      const made = await memesApi.createComics(name)
      const summary: ComicsSummary = {
        id: made.id, name: made.name, state: made.state, panelCount: 0,
        coverImageId: null, createdAt: new Date().toISOString(), builtAt: null,
      }
      setLoaded((prev) => prev !== null ? { ...prev, items: [summary, ...prev.items] } : prev)
      setCreating(false)
      setNewName("")
    } catch (error) {
      setCreateError(describeComicsError(error))
    } finally {
      setSaving(false)
    }
  }

  async function remove(id: string) {
    setDeleteError(null)
    try {
      await memesApi.deleteComics(id)
      setLoaded((prev) => prev !== null ? { ...prev, items: prev.items.filter((item) => item.id !== id) } : prev)
    } catch (error) {
      setDeleteError(describeComicsError(error))
    } finally {
      setConfirmId(null)
    }
  }

  let body: React.ReactNode
  if (current === null) {
    body = <p role="status">Loading comics...</p>
  } else if (current.failed && items.length === 0) {
    body = (
      <div role="alert">
        <p>Could not load the comics.</p>
        <button type="button" onClick={() => setReloads((n) => n + 1)} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else if (items.length === 0) {
    body = <p>{debounced === "" ? "No comics yet" : "No comics match that name"}</p>
  } else {
    body = (
      <>
        <ul className="divide-y rounded border">
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-3 p-2">
              <Link to={`/admin/comics/${item.id}`} className="flex min-w-0 flex-1 items-center gap-3">
                {item.coverImageId !== null ? (
                  <img src={memesApi.getImageUrlById(item.coverImageId)} alt={`Cover of ${item.name}`}
                       className="h-16 w-16 shrink-0 rounded object-cover" />
                ) : (
                  <div className="h-16 w-16 shrink-0 rounded bg-gray-200" aria-hidden="true" />
                )}
                <span className="min-w-0">
                  <span className="block truncate font-medium">{item.name}</span>
                  <span className="text-sm text-gray-600">
                    <span className="mr-2 rounded border px-1.5 text-xs">{item.state}</span>
                    {item.panelCount} {item.panelCount === 1 ? "panel" : "panels"}
                  </span>
                </span>
              </Link>
              {item.state === "draft" && (confirmId === item.id ? (
                <span className="flex items-center gap-2 text-sm">
                  Delete?
                  <button type="button" onClick={() => void remove(item.id)} className="min-h-11 rounded border px-3">Yes</button>
                  <button type="button" onClick={() => setConfirmId(null)} className="min-h-11 rounded border px-3">No</button>
                </span>
              ) : (
                <button type="button" aria-label={`Delete ${item.name}`}
                        onClick={() => { setDeleteError(null); setConfirmId(item.id) }}
                        className="min-h-11 rounded border px-3">Delete</button>
              ))}
            </li>
          ))}
        </ul>
        {current.failed && <p role="alert" className="mt-3">Could not load more comics.</p>}
        {current.hasMore && (
          <button type="button" disabled={loadingMore} onClick={() => void showMore()}
                  className="mt-3 min-h-11 rounded border px-3 disabled:opacity-50">Show more</button>
        )}
      </>
    )
  }

  return (
    <div>
      <h1 className="mb-4 text-xl font-semibold">Comics</h1>
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <input type="search" aria-label="Search comics" placeholder="Search by name" value={search}
               onChange={(event) => setSearch(event.target.value)}
               className="min-h-11 min-w-0 flex-1 rounded border px-3" />
        {!creating && (
          <button type="button" onClick={() => setCreating(true)} className="min-h-11 rounded border px-3">New comics</button>
        )}
      </div>
      {creating && (
        <form className="mb-4 flex flex-wrap items-end gap-3"
              onSubmit={(event) => { event.preventDefault(); void create() }}>
          <label className="flex min-w-0 flex-1 flex-col text-sm">
            Name
            <input value={newName} onChange={(event) => setNewName(event.target.value)} autoFocus
                   className="min-h-11 rounded border px-3" />
          </label>
          <button type="submit" disabled={saving} className="min-h-11 rounded border px-3 disabled:opacity-50">Create</button>
          <button type="button" onClick={() => { setCreating(false); setCreateError(null) }}
                  className="min-h-11 rounded border px-3">Cancel</button>
          {createError !== null && <p role="alert" className="w-full">{createError}</p>}
        </form>
      )}
      {deleteError !== null && <p role="alert" className="mb-3">{deleteError}</p>}
      {body}
    </div>
  )
}
