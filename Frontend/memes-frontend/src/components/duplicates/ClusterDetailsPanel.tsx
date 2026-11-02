import type { MemesApi } from '../../api/MemesApi'
import type { Meme } from '../../types/generated/all'
import { overlapPercent, sortPairs } from '../../duplicates/pairs'
import type { SimilarityEntry } from '../../duplicates/useClusterSimilarity'

type Props = {
  members: Meme[]
  entry: SimilarityEntry | undefined
  memesApi: MemesApi
  onRetry: () => void
}

export function ClusterDetailsPanel({ members, entry, memesApi, onRetry }: Props) {
  const byId = new Map(members.map(m => [m.id, m]))
  const nameOf = (id: string) => byId.get(id)?.originalFileName ?? id.slice(0, 8)

  return (
    <div className="mt-2 rounded border border-gray-200 p-3 text-sm space-y-4">
      <ul className="space-y-2">
        {members.map(meme => (
          <li key={meme.id} className="flex gap-3">
            <img src={memesApi.getImageUrl(meme)} alt="" className="h-16 w-16 object-cover rounded shrink-0" />
            <div className="min-w-0">
              <div className="font-medium break-all">{nameOf(meme.id)}</div>
              {(meme.text ?? []).length > 0
                ? (meme.text ?? []).map((line, i) => <div key={i} className="text-gray-700 break-words">{line}</div>)
                : <div className="text-gray-400 italic">no text</div>}
            </div>
          </li>
        ))}
      </ul>

      {entry?.status === 'loading' && <div className="text-gray-500">Loading pair details...</div>}

      {entry?.status === 'error' && (
        <div className="flex items-center gap-3">
          <span className="text-red-700">Could not load pair details.</span>
          <button className="min-h-11 rounded bg-gray-100 px-4 hover:bg-gray-200" onClick={onRetry}>Retry</button>
        </div>
      )}

      {entry?.status === 'ok' && entry.capped && (
        <div className="text-gray-500">Pair details are unavailable for clusters over 100 members.</div>
      )}

      {entry?.status === 'ok' && !entry.capped && (
        <ul className="space-y-2">
          {sortPairs(entry.pairs).map(pair => {
            const percent = overlapPercent(pair.overlap)
            return (
              <li
                key={`${pair.image_id1}:${pair.image_id2}`}
                aria-label={`Pair ${nameOf(pair.image_id1)} and ${nameOf(pair.image_id2)}`}
                className="flex flex-wrap items-center gap-2"
              >
                <span className="break-all">{nameOf(pair.image_id1)} / {nameOf(pair.image_id2)}</span>
                <span className="text-gray-600">{percent === null ? 'no OCR overlap data' : `OCR overlap ${percent}`}</span>
                {pair.candidates.length === 0
                  ? <span className="text-gray-400 italic">linked through other members</span>
                  : pair.candidates.map((c, i) => (
                    <span key={i} className="rounded bg-gray-100 px-2 py-0.5 text-xs">{`${c.source} ${c.distance.toFixed(3)}`}</span>
                  ))}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
