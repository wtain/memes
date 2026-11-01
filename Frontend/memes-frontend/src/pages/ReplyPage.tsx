import { useEffect, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import ActiveFilters from "../components/reply/ActiveFilters"
import AxisFacets from "../components/reply/AxisFacets"
import MessageBox from "../components/reply/MessageBox"
import ReadingPanel from "../components/reply/ReadingPanel"
import ForgetTextButton from "../components/reply/ForgetTextButton"
import NotFoundButton from "../components/NotFoundButton"
import ResultCard from "../components/reply/ResultCard"
import StatusBanner from "../components/reply/StatusBanner"
import { MemeDetailsModal } from "../components/MemeDetailsModal"
import { countFilters, hasFilters } from "../reply/filters"
import { addValue, removeValue, setSituation } from "../reply/profile"
import { labelFor, maxValuesFor } from "../reply/taxonomy"
import { useMessageSearch } from "../reply/useMessageSearch"
import { useRatings } from "../reply/useRatings"
import { useTaxonomy } from "../reply/useTaxonomy"
import type { AxisName, RatingContext } from "../reply/types"
import type { Meme } from "../types/generated/all"

type Props = {
  memesApi: MemesApi
}

export default function ReplyPage({ memesApi }: Props) {
  const search = useMessageSearch(memesApi)
  const { state } = search
  const profile = search.activeProfile
  const taxonomy = useTaxonomy(memesApi, profile?.taxonomyVersion ?? null, state.searchCount)
  const queryHash = state.meta?.queryHash ?? null
  const ratings = useRatings(memesApi, state.status === "unavailable" ? null : queryHash)
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const resultsRef = useRef<HTMLElement>(null)
  const gridRef = useRef<HTMLDivElement>(null)
  // The number of cards shown when "Show more" was activated; touched only in handlers and effects.
  const focusFromRef = useRef<number | null>(null)

  // After a "Show more" load completes, keyboard focus moves to the first new card.
  // When there is nothing new to land on (failure, unavailable, a page without new cards), focus goes to the
  // error's "Try again" button when present, otherwise to the Results section: never to the body.
  useEffect(() => {
    const from = focusFromRef.current
    if (from === null || state.loadingMore) return
    focusFromRef.current = null
    if (state.status === "error" || state.status === "unavailable") {
      const retry = document.querySelector<HTMLElement>('[role="alert"] button')
      if (retry) retry.focus()
      else resultsRef.current?.focus()
      return
    }
    if (state.items.length > from) gridRef.current?.children[from]?.querySelector<HTMLElement>("button, a[href]")?.focus()
    else resultsRef.current?.focus()
  }, [state.items.length, state.loadingMore, state.status])

  const labelOf = (axis: string, key: string) => labelFor(taxonomy, axis, key)
  const finished = state.status === "ready" || state.status === "degraded"
  const busy = state.status === "reading" || state.status === "refining"
  // The results area exists as long as a list does (even an empty one), so a refine never unmounts it.
  const showResults = (state.hasList && state.status !== "unavailable") || (busy && state.items.length > 0)
  const filterCount = countFilters(state.filters)
  const count = state.items.length
  const noun = count === 1 ? "meme" : "memes"
  const countText = finished ? (state.hasNext ? `${count} ${noun} shown, more available` : `${count} ${noun} found`) : ""

  // The text that was searched (never the box's current text), the list as shown and the reading that produced it.
  const ratingContext = (rank: number): RatingContext => ({
    text: state.submitted ?? "",
    rank,
    reading: state.listReading,
    parserVersion: state.meta?.parserVersion ?? "",
    taxonomyVersion: profile?.taxonomyVersion ?? 0,
    degraded: state.fallback !== "none",
    fallback: state.fallback,
    language: profile?.language ?? "other",
  })

  function removeFilter(axis: AxisName, value: string) {
    // The last chip unmounts itself: move focus to the stable results section first.
    if (countFilters(state.filters) === 1) resultsRef.current?.focus()
    search.setFilter(axis, state.filters[axis].filter((v) => v !== value))
  }

  function clearFilters() {
    resultsRef.current?.focus()
    search.clearFilters()
  }

  function showMore() {
    const from = state.items.length
    if (search.loadMore()) focusFromRef.current = from
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

      <p role="status" className="sr-only">{countText}</p>

      {showResults && (
        <div
          className={`flex flex-col gap-6 md:flex-row ${busy ? "opacity-60 transition-opacity motion-reduce:transition-none" : ""}`}
          aria-busy={busy}
        >
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
                onClear={clearFilters}
                onRowRemoved={() => resultsRef.current?.focus()}
              />
            </section>
          </aside>

          <section ref={resultsRef} tabIndex={-1} className="flex-1 focus:outline-none" aria-label="Results">
            <ActiveFilters filters={state.filters} taxonomy={taxonomy} onRemove={removeFilter} />

            {/* The slot is always there while results show, so the first rating cannot push the cards down under the pointer. */}
            {queryHash && state.items.length > 0 && (
              <div data-testid="forget-slot" className="mb-3 min-h-11">
                {ratings.hasRatings && <ForgetTextButton key={ratings.savedCount} onConfirm={ratings.forgetText} />}
              </div>
            )}

            {state.items.length === 0 && finished && (
              <div className="rounded-xl bg-white p-6 text-center shadow-sm">
                <p className="font-medium">No memes fit</p>
                {hasFilters(state.filters) && <p className="text-sm text-gray-600">Try removing a filter above.</p>}
              </div>
            )}

            <div ref={gridRef} className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {state.items.map((result, index) => (
                <ResultCard
                  key={result.meme.id}
                  result={result}
                  memesApi={memesApi}
                  labelOf={labelOf}
                  onOpen={() => setSelectedMeme(result.meme)}
                  rating={queryHash ? {
                    grade: ratings.grades[result.meme.id] ?? null,
                    event: ratings.lastEvent?.imageId === result.meme.id ? ratings.lastEvent.kind : null,
                    onRate: (grade) => ratings.rate(ratingContext(index + 1), result.meme.id, grade),
                  } : undefined}
                />
              ))}
            </div>

            {state.hasNext && state.status !== "error" && (
              <div className="mt-4 text-center">
                <button
                  type="button"
                  onClick={showMore}
                  aria-disabled={state.loadingMore}
                  className="min-h-11 rounded-md border bg-white px-4 py-2"
                >
                  {state.loadingMore ? "Loading…" : "Show more"}
                </button>
              </div>
            )}

            {state.searchId && finished && <NotFoundButton key={state.searchId} memesApi={memesApi} searchId={state.searchId} />}
          </section>
        </div>
      )}

      {(state.status === "reading" || (state.status === "refining" && !state.hasList)) && (
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
