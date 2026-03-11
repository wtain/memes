import { useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import FacetSidebar from "../components/FacetSidebar"
import { parseSearchParams, buildSearchParams } from "../utils/searchParams"
import { useState } from "react"
import { Facet } from "../types/facet"
import { MemesApi } from "../api/MemesApi"
import { MemesList } from "../components/MemesList"
import { useDebounce } from "../utils/useDebounce"

type SearchPageProps = {
  memesApi: MemesApi
}


export default function SearchPage({ memesApi }: SearchPageProps) {
    const [params, setParams] = useSearchParams()

    const { query, filters } = useMemo(
      () => parseSearchParams(params),
      [params]
    )

    const [debouncedQuery, setDebouncedQuery] = useDebounce<string>(query, 300);

    const [facets, setFacets] = useState<Facet[]>([])


    function updateSearch(
      nextQuery: string,
      nextFilters: Record<string, string[]>
    ) {
      setParams(buildSearchParams(nextQuery, nextFilters))
    }

    function toggleFacet(facet: string, value: string) {
      const current = filters[facet] ?? []
      const next = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value]

      updateSearch(query, { ...filters, [facet]: next })
    }

    return (
      <div className="flex gap-6">
        <FacetSidebar
          facets={facets}
          selected={filters}
          onToggle={toggleFacet}
        />

        <section className="flex-1">
          <input
            value={query}
            onChange={(e) =>
              updateSearch(e.target.value, filters)
            }
            onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setDebouncedQuery(query);
                }
            }}
            placeholder="Search memes..."
            className="w-full mb-4 rounded-md border px-3 py-2"
          />

          <MemesList
            memesApi={memesApi}
            filter={debouncedQuery}
            onFacetsChanged={(facets: Facet[]) => {
              setFacets(facets)
            }}
            tagFilters={filters}
          />

        </section>

      </div>
    )
}
