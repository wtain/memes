import { useEffect, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import { ComicsRequestError } from "../api/errors"
import { describeComicsError } from "../comics/comicsErrors"
import { movedOrder, panelSignature } from "../comics/editorReducer"
import ComicsPreview from "../components/comics/ComicsPreview"
import ComicsSearchColumn from "../components/comics/ComicsSearchColumn"
import type { AddOutcome } from "../components/comics/ComicsSearchColumn"
import PanelStrip from "../components/comics/PanelStrip"
import type { ComicsDetail } from "../types/generated/all"

type Props = { memesApi: MemesApi }

const PREVIEW_WIDTH = 1200
const PREVIEW_DEBOUNCE_MS = 400

/** The comics a load produced; `id` ties it to the route it was fetched for. */
type Loaded = { id: string; detail: ComicsDetail | null; failed: boolean }

/** Keyed by the route id, so moving between comics starts with clean editor state. */
export default function ComicsEditorPage({ memesApi }: Props) {
  const { id = "" } = useParams()
  return <ComicsEditor key={id} id={id} memesApi={memesApi} />
}

function ComicsEditor({ id, memesApi }: Props & { id: string }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  // Synchronous gate: state alone would let two quick clicks both pass before the re-render.
  const busyRef = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [renaming, setRenaming] = useState(false)
  const [nameInput, setNameInput] = useState("")

  useEffect(() => {
    let cancelled = false
    memesApi.getComics(id).then(
      (detail) => { if (!cancelled) setLoaded({ id, detail, failed: false }) },
      () => { if (!cancelled) setLoaded({ id, detail: null, failed: true }) },
    )
    return () => { cancelled = true }
  }, [memesApi, id])

  const current = loaded !== null && loaded.id === id ? loaded : null
  const detail = current?.detail ?? null
  const isDraft = detail !== null && detail.state === "draft"
  const signature = isDraft ? panelSignature(detail.panels) : null

  // Bump the preview version 400 ms after the panel list changes (not for the first load).
  const lastSignature = useRef<string | null>(null)
  useEffect(() => {
    if (signature === null) return
    if (lastSignature.current === null) { lastSignature.current = signature; return }
    if (lastSignature.current === signature) return
    lastSignature.current = signature
    const timer = setTimeout(() => setVersion((v) => v + 1), PREVIEW_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [signature])

  function replaceDetail(next: ComicsDetail) {
    setLoaded({ id, detail: next, failed: false })
  }

  async function mutate(action: () => Promise<void>) {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(describeComicsError(e))
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  async function addPanel(imageId: string): Promise<AddOutcome> {
    if (busyRef.current) return "error"
    busyRef.current = true
    setBusy(true)
    setError(null)
    try {
      replaceDetail(await memesApi.addComicsPanel(id, imageId))
      return "added"
    } catch (e) {
      if (e instanceof ComicsRequestError && (e.code === "image_in_comics" || e.code === "image_not_addable")) return "conflict"
      setError(describeComicsError(e))
      return "error"
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  function move(index: number, delta: -1 | 1) {
    if (detail === null) return
    const order = movedOrder(detail.panels.map((p) => p.id), index, delta)
    if (order === null) return
    void mutate(async () => { replaceDetail(await memesApi.reorderComicsPanels(id, order)) })
  }

  function remove(panelId: string) {
    void mutate(async () => {
      await memesApi.removeComicsPanel(id, panelId)
      replaceDetail(await memesApi.getComics(id))
    })
  }

  function saveName() {
    void mutate(async () => {
      replaceDetail(await memesApi.renameComics(id, nameInput.trim()))
      setRenaming(false)
    })
  }

  if (current === null) return <p role="status">Loading comics...</p>
  if (detail === null) {
    return <p role="alert">Could not load that comics. <Link to="/admin/comics" className="underline">Back to the list</Link></p>
  }

  const header = (
    <div className="mb-4 flex flex-wrap items-center gap-3">
      <Link to="/admin/comics" className="underline">Comics</Link>
      {renaming ? (
        <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); saveName() }}>
          <input aria-label="Comics name" value={nameInput} onChange={(event) => setNameInput(event.target.value)}
                 className="min-h-11 rounded border px-3" />
          <button type="submit" disabled={busy} className="min-h-11 rounded border px-3 disabled:opacity-50">Save name</button>
          <button type="button" onClick={() => { setRenaming(false); setError(null) }} className="min-h-11 rounded border px-3">Cancel</button>
        </form>
      ) : (
        <>
          <h1 className="text-xl font-semibold">{detail.name}</h1>
          {isDraft && (
            <button type="button" onClick={() => { setNameInput(detail.name); setRenaming(true) }}
                    className="min-h-11 rounded border px-3">Rename</button>
          )}
        </>
      )}
      <span className="rounded border px-1.5 text-xs">{detail.state}</span>
    </div>
  )

  if (!isDraft) {
    return (
      <div>
        {header}
        {detail.builtImageId !== null && (
          <div className="mb-4">
            <img src={memesApi.getImageUrlById(detail.builtImageId)} alt="Built comics" className="max-w-full rounded border" />
            <Link to={`/memes/${detail.builtImageId}`} className="mt-2 inline-block underline">Open the meme</Link>
          </div>
        )}
        <h2 className="mb-2 font-medium">Source panels</h2>
        <ol aria-label="Source panels" className="flex flex-col gap-1">
          {detail.panels.map((panel, index) => (
            <li key={panel.id} className="flex gap-3 text-sm"><span className="w-6">{index + 1}</span><span>{panel.sourceFilename}</span></li>
          ))}
        </ol>
      </div>
    )
  }

  return (
    <div>
      {header}
      {error !== null && <p role="alert" className="mb-3">{error}</p>}
      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <PanelStrip panels={detail.panels} imageUrl={(imageId) => memesApi.getImageUrlById(imageId)} busy={busy}
                      onMove={move} onRemove={remove} />
          <div className="my-4">
            {detail.panels.length === 0 ? (
              <p>No panels yet, add memes from the search</p>
            ) : (
              <ComicsPreview url={memesApi.comicsPreviewUrl(id, PREVIEW_WIDTH, String(version))} />
            )}
          </div>
          <button type="button" disabled title="Build is not available yet" className="min-h-11 rounded border px-3 opacity-50">Build</button>
        </div>
        <ComicsSearchColumn memesApi={memesApi} comicsId={id} panelSignature={signature} busy={busy} onAdd={addPanel} />
      </div>
    </div>
  )
}
