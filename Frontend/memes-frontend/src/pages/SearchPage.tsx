import { useEffect, useMemo } from "react"
import { useSearchParams } from "react-router-dom"
import { searchMemes } from "../api/search"
import FacetSidebar from "../components/FacetSidebar"
import { parseSearchParams, buildSearchParams } from "../utils/searchParams"
import { useState } from "react"
import { Meme } from "../types/meme"
import { Facet } from "../types/facet"
import MemeCard from "../components/MemeCard"
import { MemesApi } from "../api/MemesApi"
import { MemesList } from "../components/MemesList"

type SearchPageProps = {
  memesApi: MemesApi
  baseUrl: string
}


export default function SearchPage({ memesApi, baseUrl }: SearchPageProps) {
  const [params, setParams] = useSearchParams()

    const { query, filters, cursor } = useMemo(
    () => parseSearchParams(params),
    [params]
    )

    const [results, setResults] = useState<Meme[]>([])
    const [facets, setFacets] = useState<Facet[]>([])
    const [nextCursor, setNextCursor] = useState<string | undefined>()

    useEffect(() => {
    searchMemes({ query, filters, cursor }).then((res) => {
        setFacets(res.facets)
        setNextCursor(res.nextCursor)

        setResults((prev) =>
        cursor ? [...prev, ...res.results] : res.results
        )
    })
    }, [query, filters, cursor])


  useEffect(() => {
    searchMemes({ query, filters }).then((res) => {
      setResults(res.results)
      setFacets(res.facets)
    })
  }, [query, filters])

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
          placeholder="Search memes..."
          className="w-full mb-4 rounded-md border px-3 py-2"
        />

        <MemesList
          memesApi={memesApi}
          baseUrl={baseUrl}
          filter={query}
        />

      </section>

      {nextCursor && (
  <button
    className="mt-6 rounded-md border px-4 py-2"
    onClick={() =>
      setParams(
        buildSearchParams(query, filters, nextCursor)
      )
    }
  >
    Load more
  </button>
)}

    </div>
  )
}
