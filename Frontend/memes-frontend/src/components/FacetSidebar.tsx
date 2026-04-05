import { Facet } from "../types/facet"
import { MultiSelectFacet } from "./MultiSelectFacet"

type Props = {
  facets: Facet[]
  selected: Record<string, string[]>
  onFilterChange: (facet: string, values: string[]) => void
  onClear: () => void
}

export default function FacetSidebar({ facets, selected, onFilterChange, onClear }: Props) {
  const hasSelection = Object.values(selected).some(v => v.length > 0)

  return (
    <aside className="w-64 shrink-0 border-r pr-4">
      {hasSelection && (
        <button
          onClick={onClear}
          className="mb-4 text-sm text-blue-600 hover:underline"
        >
          Clear filters
        </button>
      )}


      {facets.map((facet) => {
        const selectedValues = selected[facet.name] ?? []

        return (
          <div key={facet.name} className="mb-6">
            <MultiSelectFacet
              label={facet.name}
              selected={selectedValues}
              onChange={(nextValues) => {
                onFilterChange(facet.name, nextValues)
              }}
              loadOptions={(q) =>
                Promise.resolve(
                  facet.buckets
                    .filter(b => b.value.toLowerCase().includes(q.toLowerCase()))
                    .map(b => ({ value: b.value, label: `${b.value} (${b.count})` }))
                )
              }
            />
          </div>
        )
      })}
    </aside>
  )
}