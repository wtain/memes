import { useCallback, useMemo, useState } from "react"
import { Virtuoso } from "react-virtuoso"
import MemeCard from "./MemeCard"
import { MemeDetailsModal } from "./MemeDetailsModal"
import { useWindowedPagination, type FetchPageFn } from "../hooks/useWindowedPagination"
import type { MemesApi } from "../api/MemesApi"
import type { Facet, Meme } from "../types/generated/all"

type MemesListProps = {
  memesApi: MemesApi
  filter?: string
  onFacetsChanged?: (facets: Facet[]) => void
  tagFilters?: Record<string, string[]>
  listUntagged?: boolean
  listFlagged?: boolean
  listNoOcr?: boolean
  listRecommendations?: boolean
}

const COLUMNS = 6

// Chunks `items` into rows of up to COLUMNS, aligning the first row to the item's absolute
// position in the true (unbounded) sequence -- so row boundaries stay stable and page-boundary-
// agnostic as pages are evicted from the front, matching today's seamless grid flow instead of
// visibly breaking at every page edge.
function chunkIntoAlignedRows(items: Meme[], globalStart: number): Meme[][] {
  const rows: Meme[][] = []
  if (items.length === 0) return rows
  const offset = ((globalStart % COLUMNS) + COLUMNS) % COLUMNS
  const firstRowSize = Math.min(COLUMNS - offset, items.length)
  rows.push(items.slice(0, firstRowSize))
  for (let i = firstRowSize; i < items.length; i += COLUMNS) {
    rows.push(items.slice(i, i + COLUMNS))
  }
  return rows
}

export function MemesList({ memesApi, filter, onFacetsChanged, tagFilters, listUntagged, listFlagged, listNoOcr, listRecommendations }: MemesListProps) {
  const [selectedMeme, setSelectedMeme] = useState<Meme | null>(null)

  const fetchPage: FetchPageFn = useCallback(async (cursor) => {
    if (filter && filter.length > 0 && filter.length < 2) {
      return { items: [], hasNext: false }
    }

    const tags = tagFilters
      ? Object.entries(tagFilters).flatMap(([name, values]) =>
          values.map(value => ({ category: name, name: value }))
        )
      : []

    let response
    if (listUntagged) {
      response = await memesApi.iterateUntaggedMemes(21, cursor)
    } else if (listFlagged) {
      response = await memesApi.iterateFlaggedMemes(40, cursor)
    } else if (listNoOcr) {
      response = await memesApi.iterateNoOcrMemes(21, cursor)
    } else if (listRecommendations) {
      response = await memesApi.getRecommendations(filter, 36, cursor)
    } else {
      response = await memesApi.searchMemes({ cursor, limit: 36, query: filter, tags })
    }

    if (onFacetsChanged) onFacetsChanged(response.facets ?? [])

    return {
      items: (response.items ?? []).map(item => ({ ...item, text: item.text || [], tags: item.tags || [] })),
      nextCursor: response.nextCursor,
      hasNext: response.hasNext,
    }
  }, [filter, tagFilters, memesApi, onFacetsChanged, listUntagged, listFlagged, listNoOcr, listRecommendations])

  const resetKey = `${filter ?? ""}:${JSON.stringify(tagFilters ?? {})}:${listUntagged}:${listFlagged}:${listNoOcr}:${listRecommendations}`

  const { items, firstItemIndex, hasMoreForward, hasMoreBackward, loading, loadForward, loadBackward } = useWindowedPagination({
    fetchPage,
    resetKey,
  })

  const rows = useMemo(() => chunkIntoAlignedRows(items, firstItemIndex), [items, firstItemIndex])
  const rowFirstItemIndex = Math.floor(firstItemIndex / COLUMNS)

  return (
    <div>
      <Virtuoso
        useWindowScroll
        firstItemIndex={rowFirstItemIndex}
        data={rows}
        startReached={() => { if (hasMoreBackward) loadBackward() }}
        endReached={() => { if (hasMoreForward) loadForward() }}
        itemContent={(_index, row) => (
          <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
            {row.map(meme => (
              <MemeCard key={meme.id} meme={meme} memesApi={memesApi} onClick={() => setSelectedMeme(meme)} />
            ))}
          </div>
        )}
      />

      {selectedMeme && (
        <MemeDetailsModal meme={selectedMeme} onClose={() => setSelectedMeme(null)} memesApi={memesApi} />
      )}

      {loading && items.length > 0 && (
        <div className="h-10 flex items-center justify-center"><span>Loading...</span></div>
      )}

      {items.length === 0 && !loading && (
        <div className="h-10 flex items-center justify-center">
          <span>Nothing to show</span>
        </div>
      )}
    </div>
  )
}
