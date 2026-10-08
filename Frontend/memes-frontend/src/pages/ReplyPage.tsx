import { useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import ActiveFilters from "../components/reply/ActiveFilters"
import AxisFacets from "../components/reply/AxisFacets"
import MessageBox from "../components/reply/MessageBox"
import ReadingPanel from "../components/reply/ReadingPanel"
import ResultCard from "../components/reply/ResultCard"
import StatusBanner from "../components/reply/StatusBanner"
import { MemeDetailsModal } from "../components/MemeDetailsModal"
import { countFilters, hasFilters } from "../reply/filters"
import { addValue, removeValue, setSituation } from "../reply/profile"
import { labelFor, maxValuesFor } from "../reply/taxonomy"
import { useMessageSearch } from "../reply/useMessageSearch"
import { useTaxonomy } from "../reply/useTaxonomy"
import type { AxisName } from "../reply/types"
import type { Meme } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
}

export default function ReplyPage({ memesApi }: Props) {
  const search = useMessageSearch(memesApi)
  const { state } = search
  const profile = search.activeProfile
  const taxonomy = useTaxonomy(memesApi, profile?.taxonomyVersion ?? null)
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)

  const labelOf = (axis: string, key: string) => labelFor(taxonomy, axis, key)
  const finished = state.status === "ready" || state.status === "degraded"
  const busy = state.status === "reading" || state.status === "refining"
  const showResults = finished || (busy && state.items.length > 0) || (state.status === "error" && state.items.length > 0)
  const filterCount = countFilters(state.filters)

  function removeFilter(axis: AxisName, value: string) {
    search.setFilter(axis, state.filters[axis].filter((v) => v !== value))
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <h1 className="text-2xl font-bold">Find a meme for a message</h1>

      <MessageBox value={state.text} onChange={search.setText} onSubmit={search.find} />

      <StatusBanner status={state.status} fallback={state.fallback} error={state.error} onRetry={search.retry} />

      {profile && state.status !== "unavailable" && (
        <ReadingPanel
          profile={profile}
          edited={state.editedProfile !== null}
          taxonomy={taxonomy}
          onSituation={(text) => search.editProfile((p) => setSituation(p, text))}
          onAdd={(axis, key) => search.editProfile((p) => addValue(p, axis, key, maxValuesFor(taxonomy, axis)))}
          onRemove={(axis, key) => search.editProfile((p) => removeValue(p, axis, key))}
          onReset={search.resetProfile}
        />
      )}

      <p role="status" className="sr-only">{finished ? `${state.items.length} ${state.items.length === 1 ? "meme" : "memes"} found` : ""}</p>

      {showResults && (
        <div className="flex flex-col gap-6 md:flex-row" aria-busy={busy}>
          <aside className="md:w-64 md:shrink-0">
            <button
              type="button"
              aria-expanded={filtersOpen}
              aria-controls="reply-filters"
              onClick={() => setFiltersOpen((open) => !open)}
              className="mb-2 min-h-11 w-full rounded-md border bg-white px-3 text-left md:hidden"
            >
              {`Filters${filterCount > 0 ? ` (${filterCount})` : ""}`}
            </button>
            <section
              id="reply-filters"
              role="region"
              aria-label="Filters"
              className={`${filtersOpen ? "block" : "hidden"} rounded-xl bg-white p-4 shadow-sm md:block`}
            >
              <AxisFacets
                facets={state.facets}
                selected={state.filters}
                taxonomy={taxonomy}
                onChange={search.setFilter}
                onClear={search.clearFilters}
              />
            </section>
          </aside>

          <section className="flex-1" aria-label="Results">
            <ActiveFilters filters={state.filters} taxonomy={taxonomy} onRemove={removeFilter} />

            {state.items.length === 0 && finished && (
              <div className="rounded-xl bg-white p-6 text-center shadow-sm">
                <p className="font-medium">No memes fit</p>
                {hasFilters(state.filters) && <p className="text-sm text-gray-600">Try removing a filter above.</p>}
              </div>
            )}

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {state.items.map((result) => (
                <ResultCard
                  key={result.meme.id}
                  result={result}
                  memesApi={memesApi}
                  labelOf={labelOf}
                  onOpen={() => setSelectedMeme(result.meme)}
                />
              ))}
            </div>

            {state.hasNext && (
              <div className="mt-4 text-center">
                <button
                  type="button"
                  onClick={search.loadMore}
                  disabled={state.loadingMore}
                  className="min-h-11 rounded-md border bg-white px-4 py-2"
                >
                  {state.loadingMore ? "Loading…" : "Show more"}
                </button>
              </div>
            )}
          </section>
        </div>
      )}

      {state.status === "reading" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-hidden="true">
          {[0, 1, 2].map((i) => (
            <div key={i} className="aspect-square animate-pulse rounded-xl bg-gray-200 motion-reduce:animate-none" />
          ))}
        </div>
      )}

      {selectedMeme && <MemeDetailsModal meme={selectedMeme} memesApi={memesApi} onClose={() => setSelectedMeme(null)} />}
    </div>
  )
}
