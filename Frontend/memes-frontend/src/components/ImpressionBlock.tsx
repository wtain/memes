import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { MemeImpressions } from "../types/generated/all"
import { useFetchById } from "../utils/useFetchById"

interface Props {
  imageId: string
  memesApi: MemesApi
}

/**
 * How often search showed the meme, all time and within the configured window. Renders nothing
 * while loading and on any read error, so a slow or failing read never hides the meme. The parent
 * keys it by image id.
 */
export function ImpressionBlock({ imageId, memesApi }: Props) {
  const [data, setData] = useState<MemeImpressions | null>(null)

  useFetchById(
    imageId,
    id => memesApi.getMemeImpressions(id),
    result => setData(result),
    () => setData(null),
  )

  if (!data) return null

  return (
    <div className="space-y-1">
      <div className="flex items-baseline gap-2">
        <span className="font-semibold">Shown</span>
        <span data-testid="impression-count">{data.count}</span>
        <span data-testid="impression-window" className="text-sm text-gray-500">
          {`(${data.countWindow} in the last ${data.windowDays} days)`}
        </span>
      </div>
      {data.lastShownAt && (
        <p data-testid="impression-last-shown" className="text-sm text-gray-500">
          {`last shown ${new Date(data.lastShownAt).toLocaleDateString()}`}
        </p>
      )}
    </div>
  )
}
