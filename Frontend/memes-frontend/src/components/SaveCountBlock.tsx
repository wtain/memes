import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { MemeSaves } from "../types/generated/all"
import { useFetchById } from "../utils/useFetchById"

interface Props {
  imageId: string
  memesApi: MemesApi
}

/**
 * How many times the meme was saved to a gallery from the app. Renders nothing while loading
 * and on any read error, so a slow or failing read never hides the meme. The parent keys it
 * by image id.
 */
export function SaveCountBlock({ imageId, memesApi }: Props) {
  const [data, setData] = useState<MemeSaves | null>(null)

  useFetchById(
    imageId,
    id => memesApi.getMemeSaves(id),
    result => setData(result),
    () => setData(null),
  )

  if (!data) return null

  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="font-semibold">Saves</span>
        <span data-testid="save-count-value">{data.count}</span>
      </div>
      {data.lastSavedAt && (
        <p data-testid="save-last-saved" className="text-sm text-gray-500">
          {`last saved ${new Date(data.lastSavedAt).toLocaleDateString()}`}
        </p>
      )}
    </div>
  )
}
