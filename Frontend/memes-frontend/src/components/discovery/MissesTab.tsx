import { useState } from "react"
import { Link } from "react-router-dom"
import type { MemesApi } from "../../api/MemesApi"
import { searchLink, stampText } from "../../discovery/format"
import type { DiscoveryMisses, Kind, MissesParams, MissSort } from "../../discovery/types"
import { useDiscoveryData } from "../../discovery/useDiscoveryData"
import { CheckField, NumberField, SelectField, SIZES } from "./fields"
import ResultState from "./ResultState"

const KINDS: readonly Kind[] = ["search", "meme_text", "all"]
const SORTS: readonly MissSort[] = ["times", "explicit", "last"]

export default function MissesTab({ memesApi }: { memesApi: MemesApi }) {
  const [gapMinutes, setGapMinutes] = useState<number | undefined>(5)
  const [kind, setKind] = useState<Kind>("search")
  const [sinceDays, setSinceDays] = useState<number | undefined>(undefined)
  const [minTimes, setMinTimes] = useState<number | undefined>(undefined)
  const [explicitOnly, setExplicitOnly] = useState(false)
  const [sort, setSort] = useState<MissSort>("times")
  const [limit, setLimit] = useState<number>(50)

  const params: MissesParams = {
    gapMinutes, kind, sinceDays, minTimes, explicitOnly: explicitOnly || undefined, sort, limit,
  }
  const { data, error, loading, retry } = useDiscoveryData<DiscoveryMisses>(memesApi, "misses", params)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4">
        <NumberField label="Gap minutes" value={gapMinutes} min={1} onChange={setGapMinutes} />
        <SelectField label="Kind" value={kind} options={KINDS} onChange={setKind} />
        <NumberField label="Since days" value={sinceDays} min={1} onChange={setSinceDays} />
        <NumberField label="Min times" value={minTimes} min={1} onChange={setMinTimes} />
        <CheckField label="Explicit only" checked={explicitOnly} onChange={setExplicitOnly} />
        <SelectField label="Sort" value={sort} options={SORTS} onChange={setSort} />
        <SelectField label="Show top" value={limit} options={SIZES} onChange={setLimit} />
      </div>
      <ResultState loading={loading} error={error} onRetry={retry}>
        {data && (
          <>
            {data.status === "no_schema" && (
              <p className="rounded border border-amber-300 bg-amber-50 p-2 text-sm">
                the S6 migration has not run here yet (no search_not_found table or save link), so there is no outcome data
              </p>
            )}
            {data.status === "no_outcomes" && (
              <p className="rounded border border-amber-300 bg-amber-50 p-2 text-sm">
                no linked save or press has been recorded yet, so every search would look like a miss
              </p>
            )}
            {data.status === "ok" && (
              <>
                <p className="text-xs text-gray-500">
                  Outcome data since {data.cutoff === null ? "n/a" : data.cutoff.slice(0, 10)}
                </p>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm">
                    <thead>
                      <tr className="border-b">
                        <th className="p-2">Query</th><th className="p-2">Tags</th><th className="p-2">Kind</th>
                        <th className="p-2">Sessions</th><th className="p-2">Explicit</th><th className="p-2">Searches</th>
                        <th className="p-2">First</th><th className="p-2">Last</th>
                        <th className="p-2">Web</th><th className="p-2">Android</th><th className="p-2" />
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
                            <td className="p-2">{item.explicit}</td>
                            <td className="p-2">{item.searches.toFixed(1)}</td>
                            <td className="p-2">{stampText(item.firstAt)}</td>
                            <td className="p-2">{stampText(item.lastAt)}</td>
                            <td className="p-2">{item.web}</td>
                            <td className="p-2">{item.android}</td>
                            <td className="p-2">{link && <Link to={link} className="underline">Search this</Link>}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="text-xs text-gray-500">
                  Sessions are a time-gap approximation; sessions before the outcome data began are invisible.
                </p>
              </>
            )}
          </>
        )}
      </ResultState>
    </div>
  )
}
