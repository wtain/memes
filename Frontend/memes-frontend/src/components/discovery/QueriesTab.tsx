import { useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { searchLink, stampText } from "../../discovery/format"
import type { DiscoveryQueries, Kind, QueriesParams, QuerySort } from "../../discovery/types"
import { useDiscoveryData } from "../../discovery/useDiscoveryData"
import { CheckField, NumberField, SelectField, SIZES } from "./fields"
import ResultState from "./ResultState"

const KINDS: readonly Kind[] = ["search", "meme_text", "all"]
const SORTS: readonly QuerySort[] = ["times", "last", "min_results"]

export default function QueriesTab({ memesApi }: { memesApi: MemesApi }) {
  const [kind, setKind] = useState<Kind>("search")
  const [maxResults, setMaxResults] = useState<number | undefined>(3)
  const [includeDegraded, setIncludeDegraded] = useState(false)
  const [sinceDays, setSinceDays] = useState<number | undefined>(undefined)
  const [minTimes, setMinTimes] = useState<number | undefined>(undefined)
  const [sort, setSort] = useState<QuerySort>("times")
  const [limit, setLimit] = useState<number>(50)

  const params: QueriesParams = { kind, maxResults, includeDegraded: includeDegraded || undefined, sinceDays, minTimes, sort, limit }
  const { data, error, loading, retry } = useDiscoveryData<DiscoveryQueries>(memesApi, "queries", params)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4">
        <SelectField label="Kind" value={kind} options={KINDS} onChange={setKind} />
        <NumberField label="Max results" value={maxResults} min={0} onChange={setMaxResults} />
        <CheckField label="Include degraded" checked={includeDegraded} onChange={setIncludeDegraded} />
        <NumberField label="Since days" value={sinceDays} min={1} onChange={setSinceDays} />
        <NumberField label="Min times" value={minTimes} min={1} onChange={setMinTimes} />
        <SelectField label="Sort" value={sort} options={SORTS} onChange={setSort} />
        <SelectField label="Show top" value={limit} options={SIZES} onChange={setLimit} />
      </div>
      <ResultState loading={loading} error={error} onRetry={retry}>
        {data && (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="p-2">Query</th><th className="p-2">Tags</th><th className="p-2">Kind</th>
                    <th className="p-2">Times</th><th className="p-2">First</th><th className="p-2">Last</th>
                    <th className="p-2">Min results</th><th className="p-2">Avg results</th>
                    <th className="p-2">Web</th><th className="p-2">Android</th><th className="p-2">Degraded</th>
                    <th className="p-2" />
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((item) => {
                    const link = item.query !== "" || item.tags.length > 0 ? searchLink(item.query, item.tags, item.kind) : null
                    return (
                      <tr key={`${item.kind}|${item.query}|${item.tags.join(",")}`} className="border-b align-top">
                        <td className="p-2">{item.query || "(no text)"}</td>
                        <td className="p-2">
                          <div className="flex flex-wrap gap-1">
                            {item.tags.map((tag) => <span key={tag} className="rounded bg-gray-100 px-1.5 text-xs">{tag}</span>)}
                          </div>
                        </td>
                        <td className="p-2">{item.kind}</td>
                        <td className="p-2">{item.times}</td>
                        <td className="p-2">{stampText(item.firstAt)}</td>
                        <td className="p-2">{stampText(item.lastAt)}</td>
                        <td className="p-2">{item.minResults}</td>
                        <td className="p-2">{item.avgResults.toFixed(1)}</td>
                        <td className="p-2">{item.web}</td>
                        <td className="p-2">{item.android}</td>
                        <td className="p-2">{item.degraded}{item.fallbacks.length > 0 && ` (${item.fallbacks.join(", ")})`}</td>
                        <td className="p-2">{link && <Link to={link} className="underline">Search this</Link>}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-gray-500">
              A low-result row right after a higher one from the same client is skipped (approximate).
            </p>
          </>
        )}
      </ResultState>
    </div>
  )
}
