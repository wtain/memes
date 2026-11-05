import { useRef, useState } from "react"
import type { MemesApi } from "../../api/MemesApi"
import { describeComicsError } from "../../comics/comicsErrors"
import type { ComicsBuildResult, ComicsDetail } from "../../types/generated/all"
import { Modal } from "../Modal"
import ComicsPreview from "./ComicsPreview"

type Props = {
  memesApi: MemesApi
  comics: ComicsDetail
  onClose: () => void
  /** Called with the build result once the comics is built. */
  onBuilt: (result: ComicsBuildResult) => void
  /** Called after a failed build, so the page can refresh a stale view. */
  onFailed?: (error: unknown) => void
}

const PREVIEW_WIDTH = 1200

export default function BuildDialog({ memesApi, comics, onClose, onBuilt, onFailed }: Props) {
  const [keep, setKeep] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  // Synchronous gate: state alone would let two quick clicks both pass before the re-render.
  const busyRef = useRef(false)
  const [error, setError] = useState<string | null>(null)

  function toggle(panelId: string) {
    setKeep((prev) => {
      const next = new Set(prev)
      if (next.has(panelId)) next.delete(panelId)
      else next.add(panelId)
      return next
    })
  }

  async function build() {
    if (busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setError(null)
    try {
      const keepIds = comics.panels.filter((p) => keep.has(p.id)).map((p) => p.id)
      onBuilt(await memesApi.buildComics(comics.id, keepIds))
    } catch (e) {
      setError(describeComicsError(e))
      onFailed?.(e)
    } finally {
      busyRef.current = false
      setBusy(false)
    }
  }

  return (
    <Modal title="Build comics" onClose={() => { if (!busyRef.current) onClose() }}>
      <div className="space-y-4">
        <ComicsPreview url={memesApi.comicsPreviewUrl(comics.id, PREVIEW_WIDTH, "build")} />
        <ul aria-label="Panels to keep" className="flex flex-col gap-2">
          {comics.panels.map((panel, index) => (
            <li key={panel.id}>
              <label className="flex min-h-11 items-center gap-3">
                <input type="checkbox" checked={keep.has(panel.id)} onChange={() => toggle(panel.id)}
                       aria-label={`Panel ${index + 1}: keep in library`} />
                {panel.imageId !== null && (
                  <img src={memesApi.getImageUrlById(panel.imageId)} alt={`Panel ${index + 1}`}
                       className="h-12 w-12 shrink-0 rounded object-cover" />
                )}
                <span className="w-6 font-medium">{index + 1}</span>
                <span className="text-sm">keep in library</span>
              </label>
            </li>
          ))}
        </ul>
        <p>This cannot be undone.</p>
        {error !== null && <p role="alert" className="text-red-600">{error}</p>}
        <div className="flex gap-3">
          <button type="button" disabled={busy} onClick={() => void build()}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Build comics</button>
          <button type="button" disabled={busy} onClick={onClose}
                  className="min-h-11 rounded border px-3 disabled:opacity-50">Cancel</button>
        </div>
      </div>
    </Modal>
  )
}
