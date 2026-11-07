import { useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { ComicsRequestError } from "../../api/errors"
import { describeComicsError } from "../../comics/comicsErrors"
import { defaultComicsName } from "../../comics/comicsNaming"
import type { ComicsDetail } from "../../types/generated/all"
import { useFetchById } from "../../utils/useFetchById"
import { ComicsPickerModal } from "./ComicsPickerModal"

type Props = { memesApi: MemesApi; imageId: string }

type Membership = { id: string; name: string; position: number }

/** `value` belongs to image `imageId`; a result for another image is ignored. */
type Known = { imageId: string; value: Membership | null }

/** Why image `imageId` cannot be added to a comics, in words; null when it can (or the answer is unusable). */
type Blocked = { imageId: string; message: string | null }

function blockedMessage(result: unknown, imageId: string): string | null {
  const items = (result as { items?: unknown } | null | undefined)?.items
  if (!Array.isArray(items)) return null
  const item = items.find((i): i is { imageId: string; addable: boolean; reason?: unknown } =>
    typeof i === "object" && i !== null && (i as { imageId?: unknown }).imageId === imageId)
  if (item === undefined || item.addable !== false) return null
  return describeComicsError(new ComicsRequestError(422, { code: "image_not_addable", reason: item.reason }))
}

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
  const [blocked, setBlocked] = useState<Blocked | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const navigate = useNavigate()

  useFetchById(
    imageId,
    id => memesApi.comicsByImage(id),
    result => setKnown({ imageId, value: fromResult(result) }),
    () => setKnown(null),
  )

  // Only an image in no comics can be offered; ask whether it may be added at all.
  const inNoComics = known !== null && known.imageId === imageId && known.value === null
  useFetchById(
    inNoComics ? imageId : undefined,
    id => memesApi.comicsAddability([id]),
    result => setBlocked({ imageId, message: blockedMessage(result, imageId) }),
    () => setBlocked({ imageId, message: null }),
  )

  function onAdded(comics: ComicsDetail) {
    const panel = comics.panels.find(p => p.imageId === imageId)
    setKnown({
      imageId,
      value: panel ? { id: comics.id, name: comics.name, position: panel.position } : null,
    })
  }

  /** A new draft named after the image, holding it; then the editor opens. */
  async function createComicsFromImage() {
    setCreating(true)
    setCreateError(null)
    try {
      const fileName = await memesApi.getMeme(imageId).then(m => m.originalFileName, () => undefined)
      let created: ComicsDetail
      try {
        created = await memesApi.createComics(defaultComicsName(fileName, imageId))
      } catch (e) {
        if (!(e instanceof ComicsRequestError) || e.code !== "name_taken") throw e
        created = await memesApi.createComics(defaultComicsName(fileName, imageId, true))
      }
      try {
        await memesApi.addComicsPanel(created.id, imageId)
      } catch (e) {
        // Do not leave an empty draft behind; the cleanup is best effort.
        await memesApi.deleteComics(created.id).catch(() => undefined)
        throw e
      }
      navigate(`/admin/comics/${created.id}`)
    } catch (e) {
      setCreateError(describeComicsError(e))
    } finally {
      setCreating(false)
    }
  }

  if (known === null || known.imageId !== imageId) return null
  const membership = known.value
  const answered = blocked !== null && blocked.imageId === imageId
  const blockedText = answered ? blocked.message : null

  return (
    <div className="space-y-1">
      {membership !== null ? (
        <p data-testid="comics-membership">
          In comics{" "}
          <Link to={`/admin/comics/${membership.id}`} className="text-blue-600 hover:underline">{membership.name}</Link>
          {" "}(panel {membership.position + 1})
        </p>
      ) : blockedText !== null ? (
        <p data-testid="comics-not-addable">{blockedText}</p>
      ) : answered ? (
        <div className="flex gap-2">
          <button type="button" onClick={() => setPickerOpen(true)}
            className="px-3 py-1 border rounded cursor-pointer">Add to comics…</button>
          <button type="button" disabled={creating} onClick={() => void createComicsFromImage()}
            className="px-3 py-1 border rounded cursor-pointer disabled:opacity-50">Create comics</button>
        </div>
      ) : null}
      {createError !== null && <p role="alert">{createError}</p>}
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
