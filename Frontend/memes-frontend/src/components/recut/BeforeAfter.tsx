import { useState } from "react"
import { removedShare } from "../../recut/boxOverlay"
import type { Box, Size } from "../../recut/boxOverlay"
import BoxOverlay from "./BoxOverlay"

type Props = { sourceUrl: string; cutUrl: string; box: Box }

function Missing({ what }: { what: string }) {
  return <div className="flex h-48 items-center justify-center rounded border border-dashed text-sm text-gray-500">{what} file missing</div>
}

/** Mounted with key={proposal id}, so the load state of one proposal never leaks into the next. */
export default function BeforeAfter({ sourceUrl, cutUrl, box }: Props) {
  const [size, setSize] = useState<Size | null>(null)
  const [sourceFailed, setSourceFailed] = useState(false)
  const [cutFailed, setCutFailed] = useState(false)
  const sized = size !== null && size.width > 0 && size.height > 0

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
              onLoad={(event) => setSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
              onError={() => setSourceFailed(true)}
            />
            {sized && <BoxOverlay box={box} size={size} />}
          </div>
        )}
        {sized && (
          <p className="mt-1 text-xs text-gray-600">Removes {Math.round(removedShare(box, size) * 100)}% of the source</p>
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
