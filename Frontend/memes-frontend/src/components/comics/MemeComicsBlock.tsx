import { useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import type { ComicsDetail } from "../../types/generated/all"
import { useFetchById } from "../../utils/useFetchById"
import { ComicsPickerModal } from "./ComicsPickerModal"

type Props = { memesApi: MemesApi; imageId: string }

type Membership = { id: string; name: string; position: number }

/** `value` belongs to image `imageId`; a result for another image is ignored. */
type Known = { imageId: string; value: Membership | null }

function fromResult(result: unknown): Membership | null {
  const c = (result as { comics?: Partial<Membership> | null } | null | undefined)?.comics
  if (!c || typeof c.id !== "string" || typeof c.name !== "string" || typeof c.position !== "number") return null
  return { id: c.id, name: c.name, position: c.position }
}

/**
 * Whether this image is a panel of a comics, with a picker to add it to one. Shows nothing
 * until the lookup answers (and when it fails), so a slow read never hides the meme.
 * The parent keys it by image id.
 */
export function MemeComicsBlock({ memesApi, imageId }: Props) {
  const [known, setKnown] = useState<Known | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)

  useFetchById(
    imageId,
    id => memesApi.comicsByImage(id),
    result => setKnown({ imageId, value: fromResult(result) }),
    () => setKnown(null),
  )

  function onAdded(comics: ComicsDetail) {
    const panel = comics.panels.find(p => p.imageId === imageId)
    setKnown({
      imageId,
      value: panel ? { id: comics.id, name: comics.name, position: panel.position } : null,
    })
  }

  if (known === null || known.imageId !== imageId) return null
  const membership = known.value

  return (
    <div className="space-y-1">
      {membership !== null ? (
        <p data-testid="comics-membership">
          In comics{" "}
          <Link to={`/admin/comics/${membership.id}`} className="text-blue-600 hover:underline">{membership.name}</Link>
          {" "}(panel {membership.position + 1})
        </p>
      ) : (
        <button type="button" onClick={() => setPickerOpen(true)}
          className="px-3 py-1 border rounded cursor-pointer">Add to comics…</button>
      )}
      {pickerOpen && (
        <ComicsPickerModal
          memesApi={memesApi}
          imageId={imageId}
          onClose={() => setPickerOpen(false)}
          onAdded={onAdded}
        />
      )}
    </div>
  )
}
