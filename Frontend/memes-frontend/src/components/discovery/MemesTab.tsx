import { useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { countText, per100Text, stampText } from "../../discovery/format"
import type { DiscoveryMemes, MemesParams, MemeSort } from "../../discovery/types"
import { useDiscoveryData } from "../../discovery/useDiscoveryData"
import { CheckField, NumberField, SelectField, SIZES } from "./fields"
import ResultState from "./ResultState"

const SORTS: readonly MemeSort[] = ["per_100", "redrops", "redrops_window", "popularity", "saves", "impressions"]
const SOURCES = [
  "hash_in_batch", "hash_cross_corpus", "ingest_tier_a", "ingest_tier_b", "cluster_review", "agent_review",
  "file_duplicates", "recut",
] as const

export default function MemesTab({ memesApi }: { memesApi: MemesApi }) {
  const [windowDays, setWindowDays] = useState<number | undefined>(undefined)
  const [minRedrops, setMinRedrops] = useState<number | undefined>(undefined)
  const [maxImpressions, setMaxImpressions] = useState<number | undefined>(undefined)
  const [sort, setSort] = useState<MemeSort>("per_100")
  const [limit, setLimit] = useState<number>(50)
  const [unchecked, setUnchecked] = useState<string[]>([])

  const checked = SOURCES.filter((source) => !unchecked.includes(source))
  const params: MemesParams = {
    windowDays, minRedrops, maxImpressions, sort, limit,
    sources: unchecked.length === 0 ? undefined : checked,
  }
  const { data, error, loading, retry } = useDiscoveryData<DiscoveryMemes>(memesApi, "memes", params)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4">
        <NumberField label="Window days" value={windowDays} min={1} onChange={setWindowDays} />
        <NumberField label="Min re-drops" value={minRedrops} min={0} onChange={setMinRedrops} />
        <NumberField label="Max impressions" value={maxImpressions} min={0} onChange={setMaxImpressions} />
        <SelectField label="Sort" value={sort} options={SORTS} onChange={setSort} />
        <SelectField label="Show top" value={limit} options={SIZES} onChange={setLimit} />
      </div>
      <fieldset className="flex flex-wrap items-center gap-x-4">
        <legend className="text-sm text-gray-600">Sources</legend>
        {SOURCES.map((source) => (
          <CheckField
            key={source} label={source} checked={!unchecked.includes(source)}
            onChange={(on) => setUnchecked((current) => on ? current.filter((s) => s !== source) : [...current, source])}
          />
        ))}
      </fieldset>
      <ResultState loading={loading} error={error} onRetry={retry}>
        {data && (
          <>
            {!data.impressionsAvailable && (
              <p className="rounded border border-amber-300 bg-amber-50 p-2 text-sm">
                impressions are n/a: the meme_impressions table does not exist here yet
              </p>
            )}
            <p className="text-xs text-gray-500">Window: {data.windowDays} days. {data.items.length} memes.</p>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="p-2">Meme</th><th className="p-2">File</th><th className="p-2">Re-drops (all / window)</th>
                    <th className="p-2">Sources</th><th className="p-2">Popularity</th><th className="p-2">Saves</th>
                    <th className="p-2">Impressions</th><th className="p-2">In window</th>
                    <th className="p-2">Last re-drop</th><th className="p-2">Per 100</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => (
                    <tr key={item.id} className="border-b align-top">
                      <td className="p-2">
                        <Link to={`/memes/${item.id}`}>
                          <img
                            src={memesApi.getImageUrlById(item.id)} alt={item.filename ?? item.id}
                            loading="lazy" className="h-16 w-16 object-cover"
                          />
                        </Link>
                      </td>
                      <td className="p-2">{item.filename ?? "-"}</td>
                      <td className="p-2">{item.redrops} / {item.redropsWindow}</td>
                      <td className="p-2">
                        {item.sources.map((s) => <div key={s.source} className="text-xs">{s.source}: {s.count}</div>)}
                      </td>
                      <td className="p-2">{item.popularity}</td>
                      <td className="p-2">{item.saves}</td>
                      <td className="p-2">{countText(item.impressions)}</td>
                      <td className="p-2">{countText(item.impressionsWindow)}</td>
                      <td className="p-2">{stampText(item.lastBack)}</td>
                      <td className="p-2">{per100Text(item, data.impressionsAvailable)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </ResultState>
    </div>
  )
}
