import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { describeCandidateError, isCandidateGone } from "../comics/comicsErrors"
import type { ComicsCandidate } from "../types/generated/all"

type Props = { memesApi: MemesApi }

const PAGE_SIZE = 12

type Loaded = { items: ComicsCandidate[]; hasMore: boolean; failed: boolean }

export default function ComicsCandidatesPage({ memesApi }: Props) {
  const navigate = useNavigate()
  const [reloads, setReloads] = useState(0)
  const [loaded, setLoaded] = useState<(Loaded & { reloads: number }) | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)
  // Image ids switched off, per candidate id.
  const [off, setOff] = useState<Record<string, string[]>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    memesApi.listComicsCandidates(0, PAGE_SIZE).then(
      (page) => { if (!cancelled) setLoaded({ reloads, items: page.items, hasMore: page.hasMore, failed: false }) },
      () => { if (!cancelled) setLoaded({ reloads, items: [], hasMore: false, failed: true }) },
    )
    return () => { cancelled = true }
  }, [memesApi, reloads])

  const current = loaded !== null && loaded.reloads === reloads ? loaded : null
  const items = current?.items ?? []

  async function showMore() {
    if (current === null || loadingMore) return
    setLoadingMore(true)
    try {
      const page = await memesApi.listComicsCandidates(items.length, PAGE_SIZE)
      setLoaded((prev) => prev !== null && prev.reloads === reloads
        ? { ...prev, items: [...prev.items, ...page.items.filter((c) => !prev.items.some((p) => p.id === c.id))], hasMore: page.hasMore }
        : prev)
    } catch {
      setLoaded((prev) => prev !== null && prev.reloads === reloads ? { ...prev, hasMore: false, failed: true } : prev)
    } finally {
      setLoadingMore(false)
    }
  }

  function enabledIds(candidate: ComicsCandidate): string[] {
    const hidden = off[candidate.id] ?? []
    return candidate.images.filter((image) => !hidden.includes(image.id)).map((image) => image.id)
  }

  function toggle(candidateId: string, imageId: string) {
    setOff((prev) => {
      const hidden = prev[candidateId] ?? []
      return { ...prev, [candidateId]: hidden.includes(imageId) ? hidden.filter((id) => id !== imageId) : [...hidden, imageId] }
    })
  }

  function setError(id: string, message: string | null) {
    setErrors((prev) => {
      const next = { ...prev }
      if (message === null) delete next[id]
      else next[id] = message
      return next
    })
  }

  function removeRow(candidateId: string) {
    setLoaded((prev) => prev !== null ? { ...prev, items: prev.items.filter((c) => c.id !== candidateId) } : prev)
  }

  // A failed request. When the server no longer lists the suggestion, drop the row (so "Show more" offsets stay
  // aligned with the server) and keep the message in the page-level notice; otherwise keep the row with its message.
  function fail(candidate: ComicsCandidate, error: unknown) {
    const filenames = Object.fromEntries(candidate.images.map((image) => [image.id, image.filename]))
    const message = describeCandidateError(error, filenames)
    if (isCandidateGone(error)) {
      removeRow(candidate.id)
      setNotice(`Suggestion removed: ${message}`)
    } else {
      setError(candidate.id, message)
    }
  }

  async function accept(candidate: ComicsCandidate) {
    setBusy(candidate.id)
    setError(candidate.id, null)
    setNotice(null)
    try {
      const comics = await memesApi.acceptComicsCandidate(candidate.id, enabledIds(candidate))
      navigate(`/admin/comics/${comics.id}`)
    } catch (error) {
      fail(candidate, error)
    } finally {
      setBusy(null)
    }
  }

  async function dismiss(candidate: ComicsCandidate) {
    setBusy(candidate.id)
    setError(candidate.id, null)
    setNotice(null)
    try {
      await memesApi.dismissComicsCandidate(candidate.id)
      removeRow(candidate.id)
    } catch (error) {
      fail(candidate, error)
    } finally {
      setBusy(null)
    }
  }

  let body: React.ReactNode
  if (current === null) {
    body = <p role="status">Loading suggestions...</p>
  } else if (current.failed && items.length === 0) {
    body = (
      <div role="alert">
        <p>Could not load the suggestions.</p>
        <button type="button" onClick={() => setReloads((n) => n + 1)} className="mt-2 min-h-11 rounded border px-3">Retry</button>
      </div>
    )
  } else if (items.length === 0) {
    body = <p>No suggestions. The batch job detect_comics_candidates fills this page, run it from the batches page.</p>
  } else {
    body = (
      <>
        <ul className="space-y-4">
          {items.map((candidate) => {
            const ids = enabledIds(candidate)
            const hidden = off[candidate.id] ?? []
            const disabled = busy === candidate.id
            return (
              <li key={candidate.id} aria-label={`Suggestion ${candidate.id}`} className="rounded border p-3">
                <ul className="flex flex-wrap gap-3">
                  {candidate.images.map((image) => {
                    const isOff = hidden.includes(image.id)
                    return (
                      <li key={image.id} className="w-32">
                        <button type="button" aria-pressed={!isOff} aria-label={`${isOff ? "Include" : "Exclude"} ${image.filename}`}
                                onClick={() => toggle(candidate.id, image.id)}
                                className={`block w-full rounded ${isOff ? "opacity-30" : ""}`}>
                          <img src={memesApi.getImageUrlById(image.id)} alt={image.filename}
                               className="h-28 w-32 rounded object-cover" />
                        </button>
                        <span className="block truncate text-xs" title={image.filename}>{image.filename}</span>
                        {image.width !== null && image.height !== null && (
                          <span className="block text-xs text-gray-600">{image.width}x{image.height}</span>
                        )}
                      </li>
                    )
                  })}
                </ul>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <button type="button" disabled={ids.length < 2 || disabled} onClick={() => void accept(candidate)}
                          className="min-h-11 rounded border px-3 disabled:opacity-50">Make comics</button>
                  <button type="button" disabled={disabled} onClick={() => void dismiss(candidate)}
                          className="min-h-11 rounded border px-3 disabled:opacity-50">Dismiss</button>
                </div>
                {errors[candidate.id] !== undefined && <p role="alert" className="mt-2">{errors[candidate.id]}</p>}
              </li>
            )
          })}
        </ul>
        {current.failed && <p role="alert" className="mt-3">Could not load more suggestions.</p>}
        {current.hasMore && (
          <button type="button" disabled={loadingMore} onClick={() => void showMore()}
                  className="mt-3 min-h-11 rounded border px-3 disabled:opacity-50">Show more</button>
        )}
      </>
    )
  }

  return (
    <div>
      <p className="mb-2 text-sm"><Link to="/admin/comics" className="underline">Comics</Link></p>
      <h1 className="mb-4 text-xl font-semibold">Suggestions</h1>
      {notice !== null && <p role="alert" className="mb-3">{notice}</p>}
      {body}
    </div>
  )
}
