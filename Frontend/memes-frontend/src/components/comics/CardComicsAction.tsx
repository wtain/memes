import { useState, useSyncExternalStore } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { ComicsRequestError } from "../../api/errors"
import { describeComicsError } from "../../comics/comicsErrors"
import {
  clearWorkingComics, getWorkingComics, setWorkingComics, subscribeWorkingComics,
} from "../../comics/workingComics"
import type { ComicsDetail } from "../../types/generated/all"
import { ComicsPickerModal } from "./ComicsPickerModal"

type Props = { memesApi: MemesApi; imageId: string }

/**
 * The "Add to comics" button under a result card. Without a working comics it opens the picker
 * and remembers the comics the image ends in; with one it adds straight to it.
 */
export function CardComicsAction({ memesApi, imageId }: Props) {
  const working = useSyncExternalStore(subscribeWorkingComics, getWorkingComics)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [added, setAdded] = useState<{ id: string; name: string } | null>(null)

  async function addDirect(target: { id: string; name: string }) {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await memesApi.addComicsPanel(target.id, imageId)
      setAdded(target)
    } catch (e) {
      if (e instanceof ComicsRequestError && (e.code === "built" || e.code === "not_found")) {
        clearWorkingComics()
        setError(`${describeComicsError(e)}, pick another comics`)
      } else {
        setError(describeComicsError(e))
      }
    } finally {
      setBusy(false)
    }
  }

  function onPickerAdded(comics: ComicsDetail) {
    setWorkingComics({ id: comics.id, name: comics.name })
    setAdded({ id: comics.id, name: comics.name })
  }

  return (
    <div className="px-4 py-1 text-xs border-t space-y-1">
      {added !== null ? (
        <p>
          Added to {added.name}{" "}
          <Link to={`/admin/comics/${added.id}`} className="text-blue-600 hover:underline">Open editor</Link>
        </p>
      ) : (
        <button type="button" disabled={busy}
          onClick={() => (working !== null ? void addDirect(working) : setPickerOpen(true))}
          className="px-2 py-0.5 border rounded cursor-pointer disabled:opacity-50">
          {working !== null ? `Add to ${working.name}` : "Add to comics"}
        </button>
      )}
      {error !== null && <p role="alert" className="text-red-600">{error}</p>}
      {pickerOpen && (
        <ComicsPickerModal memesApi={memesApi} imageId={imageId}
          onClose={() => setPickerOpen(false)} onAdded={onPickerAdded} />
      )}
    </div>
  )
}
