import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { MemePopularity } from "../types/generated/all"
import { useFetchById } from "../utils/useFetchById"

const SOURCE_LABELS: Record<string, string> = {
  own: "The image itself",
  hash_in_batch: "Identical files dropped together",
  hash_cross_corpus: "Identical files dropped again",
  ingest_tier_a: "Ingestion review, close match",
  ingest_tier_b: "Ingestion review, looser match",
  cluster_review: "Duplicates page",
  agent_review: "Duplicate review command",
  file_duplicates: "Identical files found in the collection",
}

/** Label for a breakdown source; an unknown key (a source added later) shows as is. */
// eslint-disable-next-line react-refresh/only-export-components -- pure helper exported for tests, per the task interface
export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

interface Props {
  imageId: string
  memesApi: MemesApi
}

/**
 * Popularity number and breakdown by source. Renders nothing while loading and on any
 * error, so a slow or failing read never hides the meme. The parent keys it by image id.
 */
export function PopularityBlock({ imageId, memesApi }: Props) {
  const [data, setData] = useState<MemePopularity | null>(null)

  useFetchById(imageId, id => memesApi.getMemePopularity(id), setData, () => setData(null))

  if (!data) return null

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
            <li key={row.source}>{`${sourceLabel(row.source)}: ${row.count}`}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
