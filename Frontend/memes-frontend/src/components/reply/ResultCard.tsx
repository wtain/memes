import { useId, useState } from "react"
import type { MemesApi } from "../../api/MemesApi"
import type { MemeFromTextResult } from "../../reply/types"
import MemeCard from "../MemeCard"
import ExplanationPanel from "./ExplanationPanel"

type Props = {
  result: MemeFromTextResult
  memesApi: MemesApi
  labelOf: (axis: string, key: string) => string
  onOpen: () => void
}

export default function ResultCard({ result, memesApi, labelOf, onOpen }: Props) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const similar = (result.clusterSize ?? 1) - 1

  return (
    <div className="flex flex-col">
      <MemeCard meme={result.meme} memesApi={memesApi} onClick={onOpen} />
      <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
        {similar > 0 && <span className="rounded bg-gray-200 px-2 py-0.5">+{similar} similar</span>}
        {!result.axisPath && <span className="rounded bg-yellow-100 px-2 py-0.5">Matched by words only</span>}
        <button
          type="button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen((o) => !o)}
          className="min-h-11 text-blue-600 hover:underline"
        >
          Why this meme
        </button>
      </div>
      {open && (
        <div id={panelId}>
          <ExplanationPanel result={result} labelOf={labelOf} />
        </div>
      )}
    </div>
  )
}
