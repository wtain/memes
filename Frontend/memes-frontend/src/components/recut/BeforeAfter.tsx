import { useState } from "react"
import { removedShare } from "../../recut/boxOverlay"
import type { Box, Size } from "../../recut/boxOverlay"
import BoxEditor from "./BoxEditor"
import BoxOverlay from "./BoxOverlay"

/** `draft`: while the owner adjusts the box, the box being edited (the overlay becomes the editor); null or absent otherwise. */
type Props = {
  sourceUrl: string
  cutUrl: string
  box: Box
  draft?: Box | null
  onDraftChange?: (box: Box) => void
  /** Called with the natural size of the source once it has loaded (the page validates a draft against it). */
  onSize?: (size: Size) => void
}

function Missing({ what }: { what: string }) {
  return <div className="flex h-48 items-center justify-center rounded border border-dashed text-sm text-gray-500">{what} file missing</div>
}

/** Mounted with a key of the proposal and its cut image, so the load state of one never leaks into the next. */
export default function BeforeAfter({ sourceUrl, cutUrl, box, draft = null, onDraftChange, onSize }: Props) {
  const [size, setSize] = useState<Size | null>(null)
  const [sourceFailed, setSourceFailed] = useState(false)
  const [cutFailed, setCutFailed] = useState(false)
  const sized = size !== null && size.width > 0 && size.height > 0
  const shown = draft ?? box

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <figure aria-label="Source with the proposed box">
        <figcaption className="mb-1 text-sm font-medium">Source</figcaption>
        {sourceFailed ? (
          <Missing what="Source" />
        ) : (
          <div className="relative inline-block max-w-full">
            <img
              src={sourceUrl}
              alt="Source image"
              className="block max-h-[70vh] max-w-full"
              onLoad={(event) => {
                const loaded = { width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight }
                setSize(loaded)
                onSize?.(loaded)
              }}
              onError={() => setSourceFailed(true)}
            />
            {sized && (draft !== null
              ? <BoxEditor box={draft} size={size} onChange={onDraftChange ?? (() => {})} />
              : <BoxOverlay box={box} size={size} />)}
          </div>
        )}
        {sized && (
          <p className="mt-1 text-xs text-gray-600">Removes {Math.round(removedShare(shown, size) * 100)}% of the source</p>
        )}
      </figure>
      <figure aria-label="Proposed cut">
        <figcaption className="mb-1 text-sm font-medium">Cut</figcaption>
        {cutFailed ? (
          <Missing what="Cut" />
        ) : (
          <img src={cutUrl} alt="Proposed cut" className="block max-h-[70vh] max-w-full" onError={() => setCutFailed(true)} />
        )}
      </figure>
    </div>
  )
}
