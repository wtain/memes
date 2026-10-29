import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { MemePopularity } from "../types/generated/all"
import { useFetchById } from "../utils/useFetchById"
import { formatSourceCount, sourceLabel } from "../utils/popularitySources"

interface Props {
  imageId: string
  memesApi: MemesApi
}

const MIN_POPULARITY = 1
const MAX_POPULARITY = 1000000

/**
 * Popularity number and breakdown by source, with a control to set the number by hand
 * (not for a discarded image). Renders nothing while loading and on any read error, so a
 * slow or failing read never hides the meme. The parent keys it by image id.
 */
export function PopularityBlock({ imageId, memesApi }: Props) {
  const [data, setData] = useState<MemePopularity | null>(null)
  const [draft, setDraft] = useState("")
  const [saving, setSaving] = useState(false)
  const [saveFailed, setSaveFailed] = useState(false)

  useFetchById(
    imageId,
    id => memesApi.getMemePopularity(id),
    result => {
      setData(result)
      setDraft(String(result.popularity))
    },
    () => setData(null),
  )

  if (!data) return null

  const target = draft.trim() === "" ? NaN : Number(draft)
  const valid = Number.isInteger(target) && target >= MIN_POPULARITY && target <= MAX_POPULARITY
  const canSave = valid && target !== data.popularity && !saving

  const save = async () => {
    setSaving(true)
    setSaveFailed(false)
    try {
      const updated = await memesApi.setMemePopularity(imageId, target)
      setData(updated)
      setDraft(String(updated.popularity))
    } catch {
      setSaveFailed(true)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="font-semibold">Popularity</span>
        <span data-testid="popularity-value">{data.popularity}</span>
        {data.status !== "normal" && (
          <span data-testid="popularity-status" className="text-xs text-gray-500">{data.status}</span>
        )}
      </div>
      {data.breakdown.length > 0 && (
        <ul className="text-sm text-gray-700">
          {data.breakdown.map(row => (
            <li key={row.source}>{`${sourceLabel(row.source)}: ${formatSourceCount(row.source, row.count)}`}</li>
          ))}
        </ul>
      )}
      {data.status !== "discarded" && (
        <div className="flex items-center gap-2">
          <input
            type="number"
            aria-label="Set popularity"
            className="w-28 border rounded px-2 py-1 text-sm"
            min={MIN_POPULARITY}
            max={MAX_POPULARITY}
            step={1}
            value={draft}
            onChange={e => setDraft(e.target.value)}
          />
          <button
            type="button"
            className="border rounded px-2 py-1 text-sm disabled:opacity-50"
            disabled={!canSave}
            onClick={save}
          >
            Save
          </button>
        </div>
      )}
      {saveFailed && (
        <p role="alert" className="text-sm text-red-600">Could not save the popularity. Try again.</p>
      )}
    </div>
  )
}
