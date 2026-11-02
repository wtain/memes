import { useSearchParams } from "react-router-dom"
import type { MemesApi } from "../api/MemesApi"
import MemesTab from "../components/discovery/MemesTab"
import MissesTab from "../components/discovery/MissesTab"
import QueriesTab from "../components/discovery/QueriesTab"

const TABS = [
  { view: "memes", label: "Memes" },
  { view: "queries", label: "Queries" },
  { view: "misses", label: "Misses" },
] as const

type Props = { memesApi: MemesApi }

export default function DiscoveryPage({ memesApi }: Props) {
  const [searchParams, setSearchParams] = useSearchParams()
  const requested = searchParams.get("view")
  const active = TABS.find((tab) => tab.view === requested)?.view ?? "memes"

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Discovery report</h1>
      <div role="tablist" aria-label="Discovery views" className="flex gap-2">
        {TABS.map((tab) => (
          <button
            key={tab.view} type="button" role="tab" aria-selected={tab.view === active}
            className={`min-h-11 rounded border px-4 ${tab.view === active ? "bg-blue-600 text-white" : "bg-white"}`}
            onClick={() => setSearchParams({ view: tab.view })}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {active === "memes" && <MemesTab memesApi={memesApi} />}
      {active === "queries" && <QueriesTab memesApi={memesApi} />}
      {active === "misses" && <MissesTab memesApi={memesApi} />}
    </div>
  )
}
